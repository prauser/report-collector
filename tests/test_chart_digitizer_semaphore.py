"""Tests for the semaphore deadlock fix in chart_digitizer._digitize_single.

Verifies that:
1. The semaphore is released BEFORE the 60-second backoff sleep on 429 errors.
2. asyncio.shield() is used around the Gemini generate_content call.
3. On TimeoutError, semaphore is released and (None, 0, 0) is returned.
4. Non-429 GeminiClientError releases semaphore and returns (None, 0, 0).
5. Concurrent callers are not blocked during backoff (semaphore slot is free).
"""
from __future__ import annotations

import asyncio
import inspect
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from parser.rate_limit import RateLimitGate


def run(coro):
    """Helper: run a coroutine in a new event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_gemini_rate_limit_error():
    """Create a google.genai ClientError that looks like a 429."""
    from google.genai.errors import ClientError
    err = ClientError.__new__(ClientError)
    err.code = 429
    err.message = "quota exceeded"
    err.status = "RESOURCE_EXHAUSTED"
    err.response = None
    Exception.__init__(err, "429 quota exceeded")
    return err


def _make_gemini_non_rate_limit_error():
    """Create a google.genai ClientError that is NOT a 429 (e.g. 403)."""
    from google.genai.errors import ClientError
    err = ClientError.__new__(ClientError)
    err.code = 403
    err.message = "forbidden"
    err.status = "PERMISSION_DENIED"
    err.response = None
    Exception.__init__(err, "403 forbidden")
    return err


class TestSemaphoreReleasedBeforeBackoff:
    """Core: semaphore must be free during the backoff sleep."""

    def test_semaphore_released_before_trigger_backoff(self):
        """trigger_backoff is called AFTER the semaphore has been released.

        We verify by checking the semaphore value at the moment trigger_backoff
        is invoked. If the semaphore slot has been released, its value should
        equal the initial value (5) or greater than zero (i.e. available).
        """
        import parser.chart_digitizer as mod

        async def _test():
            rate_limit_err = _make_gemini_rate_limit_error()
            semaphore_value_during_backoff = []

            async def fake_backoff(retry_after=60.0):
                # At this point the semaphore should already be released
                semaphore_value_during_backoff.append(mod._SEMAPHORE._value)

            image = MagicMock()
            image.image_bytes = b"fake"
            image.page_num = 0
            image.source = "test"

            with patch.object(mod._gemini_chart_gate, "trigger_backoff", side_effect=fake_backoff), \
                 patch("parser.chart_digitizer.settings") as s, \
                 patch("google.genai.Client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = AsyncMock(
                    side_effect=rate_limit_err
                )
                mock_genai_client.return_value = mock_client_instance

                text, in_tok, out_tok = await mod._digitize_single(image)

            # trigger_backoff should have been called (at least once)
            assert len(semaphore_value_during_backoff) >= 1
            # When trigger_backoff ran, the semaphore must have been released
            # (value > 0 means slot is available, i.e. NOT held)
            assert all(v > 0 for v in semaphore_value_during_backoff), (
                f"Semaphore was still held during backoff! Values: {semaphore_value_during_backoff}"
            )

        run(_test())

    def test_semaphore_not_held_during_60s_sleep(self):
        """During the asyncio.sleep inside trigger_backoff, the semaphore is free."""
        import parser.chart_digitizer as mod

        async def _test():
            rate_limit_err = _make_gemini_rate_limit_error()
            semaphore_during_sleep = []

            async def fake_sleep(seconds):
                semaphore_during_sleep.append(mod._SEMAPHORE._value)

            image = MagicMock()
            image.image_bytes = b"fake"
            image.page_num = 0
            image.source = "test"

            # Patch both trigger_backoff's internal sleep AND _gemini_chart_gate
            # We need trigger_backoff to actually run (not be mocked) but with a
            # fast sleep so we can inspect state during it.
            with patch("parser.rate_limit.asyncio.sleep", side_effect=fake_sleep), \
                 patch("parser.chart_digitizer.settings") as s, \
                 patch("google.genai.Client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = AsyncMock(
                    side_effect=rate_limit_err
                )
                mock_genai_client.return_value = mock_client_instance

                text, in_tok, out_tok = await mod._digitize_single(image)

            # asyncio.sleep was called (inside trigger_backoff)
            assert len(semaphore_during_sleep) >= 1
            # Semaphore must be available (not held) during sleep
            assert all(v > 0 for v in semaphore_during_sleep), (
                f"Semaphore was held during sleep! Values: {semaphore_during_sleep}"
            )

        run(_test())

    def test_concurrent_caller_not_blocked_during_backoff(self):
        """A second concurrent _digitize_single call can proceed while first backs off.

        With the old (broken) code, the semaphore would be held during the 60-second
        backoff, blocking the second caller. With the fix, the semaphore is released
        before backoff, so the second caller can acquire the semaphore and run.

        trigger_backoff is mocked to instant so no real lock/event state is left
        behind that could interfere with subsequent tests.
        """
        import parser.chart_digitizer as mod

        async def _test():
            rate_limit_err = _make_gemini_rate_limit_error()

            image1 = MagicMock()
            image1.image_bytes = b"fake1"
            image1.page_num = 0
            image1.source = "test"

            image2 = MagicMock()
            image2.image_bytes = b"fake2"
            image2.page_num = 1
            image2.source = "test"

            mock_response = MagicMock()
            mock_response.text = "| col | val |"
            mock_response.usage_metadata = MagicMock(
                prompt_token_count=5, candidates_token_count=3
            )

            call_count = 0

            async def fail_first_succeed_rest(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise rate_limit_err
                return mock_response

            with patch.object(mod._gemini_chart_gate, "trigger_backoff", new_callable=AsyncMock), \
                 patch("parser.chart_digitizer.settings") as s, \
                 patch("google.genai.Client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = fail_first_succeed_rest
                mock_genai_client.return_value = mock_client_instance

                # Run both concurrently
                results = await asyncio.gather(
                    mod._digitize_single(image1),
                    mod._digitize_single(image2),
                    return_exceptions=True,
                )

            # Both should complete (not deadlock)
            assert len(results) == 2
            # At least one should return a valid result (image2)
            non_exception = [r for r in results if not isinstance(r, Exception)]
            assert len(non_exception) >= 1

        run(_test())


class TestAsyncioShieldUsed:
    """asyncio.shield() must wrap the Gemini generate_content call."""

    def test_asyncio_shield_used_in_source(self):
        """Source code of _digitize_single must use asyncio.shield."""
        import parser.chart_digitizer as mod
        source = inspect.getsource(mod._digitize_single)
        assert "asyncio.shield(" in source, (
            "_digitize_single must use asyncio.shield() around generate_content"
        )

    def test_timeout_does_not_cancel_underlying_request(self):
        """On TimeoutError, the shielded request continues; (None, 0, 0) returned.

        asyncio.shield() ensures the underlying coroutine is NOT cancelled when
        wait_for times out. We verify that (None, 0, 0) is returned on timeout.
        The slow_generate coroutine is made to complete shortly after the timeout
        so the background task does not orphan the event loop.
        """
        import parser.chart_digitizer as mod

        async def _test():
            # The generate_content coroutine sleeps briefly then returns;
            # wait_for will time out before it finishes (timeout=0.01s < sleep=0.1s)
            async def slow_generate(*args, **kwargs):
                await asyncio.sleep(0.1)
                return MagicMock()

            image = MagicMock()
            image.image_bytes = b"fake"
            image.page_num = 0
            image.source = "test"

            with patch("parser.chart_digitizer._GEMINI_TIMEOUT", 0.01), \
                 patch("parser.chart_digitizer.settings") as s, \
                 patch("google.genai.Client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = slow_generate
                mock_genai_client.return_value = mock_client_instance

                text, in_tok, out_tok = await mod._digitize_single(image)

            # Let the shielded background task finish so the event loop stays clean
            await asyncio.sleep(0.15)

            assert text is None
            assert in_tok == 0
            assert out_tok == 0

        run(_test())

    def test_semaphore_released_on_timeout(self):
        """After a TimeoutError, the semaphore slot is returned."""
        import parser.chart_digitizer as mod

        async def _test():
            async def slow_generate(*args, **kwargs):
                await asyncio.sleep(0.1)
                return MagicMock()

            image = MagicMock()
            image.image_bytes = b"fake"
            image.page_num = 0
            image.source = "test"

            initial_semaphore_value = mod._SEMAPHORE._value

            with patch("parser.chart_digitizer._GEMINI_TIMEOUT", 0.01), \
                 patch("parser.chart_digitizer.settings") as s, \
                 patch("google.genai.Client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = slow_generate
                mock_genai_client.return_value = mock_client_instance

                await mod._digitize_single(image)

            # Let the shielded background task finish so the event loop stays clean
            await asyncio.sleep(0.15)

            # Semaphore must be restored to initial value
            assert mod._SEMAPHORE._value == initial_semaphore_value, (
                "Semaphore was not released after TimeoutError"
            )

        run(_test())


class TestSemaphoreReleasedOnAllErrorPaths:
    """Semaphore must be released on every error path."""

    def test_semaphore_released_on_non_429_gemini_error(self):
        """Non-429 GeminiClientError releases semaphore and returns (None, 0, 0)."""
        import parser.chart_digitizer as mod

        async def _test():
            non_429_err = _make_gemini_non_rate_limit_error()

            image = MagicMock()
            image.image_bytes = b"fake"
            image.page_num = 0
            image.source = "test"

            initial_semaphore_value = mod._SEMAPHORE._value

            with patch("parser.chart_digitizer.settings") as s, \
                 patch("google.genai.Client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = AsyncMock(
                    side_effect=non_429_err
                )
                mock_genai_client.return_value = mock_client_instance

                text, in_tok, out_tok = await mod._digitize_single(image)

            assert text is None
            assert in_tok == 0
            assert out_tok == 0
            assert mod._SEMAPHORE._value == initial_semaphore_value, (
                "Semaphore was not released after non-429 GeminiClientError"
            )

        run(_test())

    def test_semaphore_released_on_generic_exception(self):
        """Generic RuntimeError releases semaphore and returns (None, 0, 0)."""
        import parser.chart_digitizer as mod

        async def _test():
            image = MagicMock()
            image.image_bytes = b"fake"
            image.page_num = 0
            image.source = "test"

            initial_semaphore_value = mod._SEMAPHORE._value

            with patch("parser.chart_digitizer.settings") as s, \
                 patch("google.genai.Client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = AsyncMock(
                    side_effect=RuntimeError("something went wrong")
                )
                mock_genai_client.return_value = mock_client_instance

                text, in_tok, out_tok = await mod._digitize_single(image)

            assert text is None
            assert in_tok == 0
            assert out_tok == 0
            assert mod._SEMAPHORE._value == initial_semaphore_value, (
                "Semaphore was not released after generic exception"
            )

        run(_test())

    def test_semaphore_released_on_success(self):
        """After a successful call, the semaphore slot is returned."""
        import parser.chart_digitizer as mod

        async def _test():
            mock_response = MagicMock()
            mock_response.text = "| col | val |"
            mock_response.usage_metadata = MagicMock(
                prompt_token_count=10, candidates_token_count=5
            )

            image = MagicMock()
            image.image_bytes = b"fake"
            image.page_num = 0
            image.source = "test"

            initial_semaphore_value = mod._SEMAPHORE._value

            with patch("parser.chart_digitizer.settings") as s, \
                 patch("google.genai.Client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = AsyncMock(
                    return_value=mock_response
                )
                mock_genai_client.return_value = mock_client_instance

                text, in_tok, out_tok = await mod._digitize_single(image)

            assert text == "| col | val |"
            assert mod._SEMAPHORE._value == initial_semaphore_value, (
                "Semaphore was not released after successful call"
            )

        run(_test())


class TestGateWaitCalledPerAttempt:
    """gate.wait() is called on each retry attempt (inside the loop)."""

    def test_gate_wait_called_twice_on_retry(self):
        """On a 429 retry, gate.wait() is called once per attempt (twice total)."""
        import parser.chart_digitizer as mod

        async def _test():
            rate_limit_err = _make_gemini_rate_limit_error()
            wait_count = []
            original_wait = mod._gemini_chart_gate.wait

            async def tracking_wait():
                wait_count.append(True)
                await original_wait()

            mock_response = MagicMock()
            mock_response.text = "| x | y |"
            mock_response.usage_metadata = MagicMock(
                prompt_token_count=5, candidates_token_count=3
            )

            call_count = 0

            async def fail_then_succeed(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise rate_limit_err
                return mock_response

            image = MagicMock()
            image.image_bytes = b"fake"
            image.page_num = 0
            image.source = "test"

            with patch.object(mod._gemini_chart_gate, "wait", side_effect=tracking_wait), \
                 patch.object(mod._gemini_chart_gate, "trigger_backoff", new_callable=AsyncMock), \
                 patch("parser.chart_digitizer.settings") as s, \
                 patch("google.genai.Client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = fail_then_succeed
                mock_genai_client.return_value = mock_client_instance

                text, in_tok, out_tok = await mod._digitize_single(image)

            # gate.wait() called once per attempt: attempt 0 + attempt 1 = 2
            assert len(wait_count) == 2, (
                f"gate.wait() should be called once per attempt; called {len(wait_count)} times"
            )
            assert text == "| x | y |"

        run(_test())
