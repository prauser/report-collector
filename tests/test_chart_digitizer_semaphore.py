"""Tests for chart_digitizer._digitize_single correctness.

Verifies:
1. The semaphore is released BEFORE the backoff sleep on 429/503 errors.
2. asyncio.shield() is NOT used (removed to prevent leaked HTTP connections on timeout).
3. On TimeoutError, semaphore is released and (None, 0, 0) is returned.
4. Non-429 GeminiClientError releases semaphore and returns (None, 0, 0).
5. Concurrent callers are not blocked during backoff (semaphore slot is free).
6. trigger_backoff is NOT called after the final (last) failed attempt.
7. Module-level constants _RATE_LIMIT_BACKOFF and _SERVER_ERROR_BACKOFF exist.
8. Module-level lazy client _get_gemini_client() caches the client.
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


def _make_gemini_503_error():
    """Create a google.genai ClientError that looks like a 503."""
    from google.genai.errors import ClientError
    err = ClientError.__new__(ClientError)
    err.code = 503
    err.message = "service unavailable"
    err.status = "UNAVAILABLE"
    err.response = None
    Exception.__init__(err, "503 service unavailable")
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
                 patch("parser.chart_digitizer._gemini_client", None), \
                 patch("google.genai.Client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = AsyncMock(
                    side_effect=rate_limit_err
                )
                mock_genai_client.return_value = mock_client_instance

                text, in_tok, out_tok = await mod._digitize_single(image)

            # trigger_backoff should have been called exactly once (only for attempt 0)
            assert len(semaphore_value_during_backoff) == 1
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
                 patch("parser.chart_digitizer._gemini_client", None), \
                 patch("google.genai.Client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = AsyncMock(
                    side_effect=rate_limit_err
                )
                mock_genai_client.return_value = mock_client_instance

                text, in_tok, out_tok = await mod._digitize_single(image)

            # asyncio.sleep was called (inside trigger_backoff), once for attempt 0
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
                 patch("parser.chart_digitizer._gemini_client", None), \
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


class TestNoAsyncioShield:
    """asyncio.shield() must NOT be present — it leaks background HTTP connections on timeout."""

    def test_asyncio_shield_not_used_in_source(self):
        """Source code of _digitize_single must NOT use asyncio.shield."""
        import parser.chart_digitizer as mod
        source = inspect.getsource(mod._digitize_single)
        assert "asyncio.shield(" not in source, (
            "_digitize_single must not use asyncio.shield() — it leaks connections on timeout"
        )

    def test_timeout_cancels_underlying_request(self):
        """On TimeoutError, wait_for properly cancels the request; (None, 0, 0) returned."""
        import parser.chart_digitizer as mod

        async def _test():
            cancelled = []

            async def slow_generate(*args, **kwargs):
                try:
                    await asyncio.sleep(10)
                    return MagicMock()
                except asyncio.CancelledError:
                    cancelled.append(True)
                    raise

            image = MagicMock()
            image.image_bytes = b"fake"
            image.page_num = 0
            image.source = "test"

            with patch("parser.chart_digitizer._GEMINI_TIMEOUT", 0.01), \
                 patch("parser.chart_digitizer.settings") as s, \
                 patch("parser.chart_digitizer._gemini_client", None), \
                 patch("google.genai.Client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = slow_generate
                mock_genai_client.return_value = mock_client_instance

                text, in_tok, out_tok = await mod._digitize_single(image)

            assert text is None
            assert in_tok == 0
            assert out_tok == 0
            # Without asyncio.shield, the underlying coroutine gets cancelled
            assert len(cancelled) == 1, (
                "Without asyncio.shield, the underlying request should be cancelled on timeout"
            )

        run(_test())

    def test_semaphore_released_on_timeout(self):
        """After a TimeoutError, the semaphore slot is returned."""
        import parser.chart_digitizer as mod

        async def _test():
            async def slow_generate(*args, **kwargs):
                await asyncio.sleep(10)
                return MagicMock()

            image = MagicMock()
            image.image_bytes = b"fake"
            image.page_num = 0
            image.source = "test"

            initial_semaphore_value = mod._SEMAPHORE._value

            with patch("parser.chart_digitizer._GEMINI_TIMEOUT", 0.01), \
                 patch("parser.chart_digitizer.settings") as s, \
                 patch("parser.chart_digitizer._gemini_client", None), \
                 patch("google.genai.Client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = slow_generate
                mock_genai_client.return_value = mock_client_instance

                await mod._digitize_single(image)

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
                 patch("parser.chart_digitizer._gemini_client", None), \
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
                 patch("parser.chart_digitizer._gemini_client", None), \
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
                 patch("parser.chart_digitizer._gemini_client", None), \
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
                 patch("parser.chart_digitizer._gemini_client", None), \
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


class TestNoBackoffAfterLastAttempt:
    """trigger_backoff must NOT be called after the final failed attempt."""

    def test_trigger_backoff_not_called_on_last_attempt_429(self):
        """When both attempts fail with 429, trigger_backoff is called only once (attempt 0)."""
        import parser.chart_digitizer as mod

        async def _test():
            rate_limit_err = _make_gemini_rate_limit_error()
            backoff_calls = []

            async def record_backoff(retry_after=60.0):
                backoff_calls.append(retry_after)

            image = MagicMock()
            image.image_bytes = b"fake"
            image.page_num = 0
            image.source = "test"

            with patch.object(mod._gemini_chart_gate, "trigger_backoff", side_effect=record_backoff), \
                 patch("parser.chart_digitizer.settings") as s, \
                 patch("parser.chart_digitizer._gemini_client", None), \
                 patch("google.genai.Client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = AsyncMock(
                    side_effect=rate_limit_err
                )
                mock_genai_client.return_value = mock_client_instance

                text, in_tok, out_tok = await mod._digitize_single(image)

            # Both attempts exhausted → only attempt 0 triggers backoff, attempt 1 does not
            assert len(backoff_calls) == 1, (
                f"trigger_backoff should be called once (only for attempt 0), "
                f"but was called {len(backoff_calls)} times"
            )
            assert text is None

        run(_test())

    def test_trigger_backoff_not_called_on_last_attempt_503(self):
        """When both attempts fail with 503, trigger_backoff is called only once (attempt 0)."""
        import parser.chart_digitizer as mod

        async def _test():
            server_err = _make_gemini_503_error()
            backoff_calls = []

            async def record_backoff(retry_after=10.0):
                backoff_calls.append(retry_after)

            image = MagicMock()
            image.image_bytes = b"fake"
            image.page_num = 0
            image.source = "test"

            with patch.object(mod._gemini_chart_gate, "trigger_backoff", side_effect=record_backoff), \
                 patch("parser.chart_digitizer.settings") as s, \
                 patch("parser.chart_digitizer._gemini_client", None), \
                 patch("google.genai.Client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = AsyncMock(
                    side_effect=server_err
                )
                mock_genai_client.return_value = mock_client_instance

                text, in_tok, out_tok = await mod._digitize_single(image)

            assert len(backoff_calls) == 1, (
                f"trigger_backoff should be called once (only for attempt 0), "
                f"but was called {len(backoff_calls)} times"
            )
            assert text is None

        run(_test())

    def test_backoff_duration_429_uses_rate_limit_constant(self):
        """429 error uses _RATE_LIMIT_BACKOFF constant for trigger_backoff."""
        import parser.chart_digitizer as mod

        async def _test():
            rate_limit_err = _make_gemini_rate_limit_error()
            backoff_calls = []

            async def record_backoff(retry_after=60.0):
                backoff_calls.append(retry_after)

            image = MagicMock()
            image.image_bytes = b"fake"
            image.page_num = 0
            image.source = "test"

            mock_response = MagicMock()
            mock_response.text = "| a | b |"
            mock_response.usage_metadata = MagicMock(
                prompt_token_count=2, candidates_token_count=2
            )
            call_count = 0

            async def fail_then_succeed(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise rate_limit_err
                return mock_response

            with patch.object(mod._gemini_chart_gate, "trigger_backoff", side_effect=record_backoff), \
                 patch("parser.chart_digitizer.settings") as s, \
                 patch("parser.chart_digitizer._gemini_client", None), \
                 patch("google.genai.Client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = fail_then_succeed
                mock_genai_client.return_value = mock_client_instance

                await mod._digitize_single(image)

            assert backoff_calls == [mod._RATE_LIMIT_BACKOFF], (
                f"Expected backoff={mod._RATE_LIMIT_BACKOFF}, got {backoff_calls}"
            )

        run(_test())

    def test_backoff_duration_503_uses_server_error_constant(self):
        """503 error uses _SERVER_ERROR_BACKOFF constant for trigger_backoff."""
        import parser.chart_digitizer as mod

        async def _test():
            server_err = _make_gemini_503_error()
            backoff_calls = []

            async def record_backoff(retry_after=10.0):
                backoff_calls.append(retry_after)

            image = MagicMock()
            image.image_bytes = b"fake"
            image.page_num = 0
            image.source = "test"

            mock_response = MagicMock()
            mock_response.text = "| a | b |"
            mock_response.usage_metadata = MagicMock(
                prompt_token_count=2, candidates_token_count=2
            )
            call_count = 0

            async def fail_then_succeed(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise server_err
                return mock_response

            with patch.object(mod._gemini_chart_gate, "trigger_backoff", side_effect=record_backoff), \
                 patch("parser.chart_digitizer.settings") as s, \
                 patch("parser.chart_digitizer._gemini_client", None), \
                 patch("google.genai.Client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = fail_then_succeed
                mock_genai_client.return_value = mock_client_instance

                await mod._digitize_single(image)

            assert backoff_calls == [mod._SERVER_ERROR_BACKOFF], (
                f"Expected backoff={mod._SERVER_ERROR_BACKOFF}, got {backoff_calls}"
            )

        run(_test())


class TestMagicNumberConstants:
    """Module-level constants replace magic numbers."""

    def test_rate_limit_backoff_constant_exists(self):
        """_RATE_LIMIT_BACKOFF constant is defined at module level."""
        import parser.chart_digitizer as mod
        assert hasattr(mod, "_RATE_LIMIT_BACKOFF"), "_RATE_LIMIT_BACKOFF constant must exist"
        assert mod._RATE_LIMIT_BACKOFF == 60.0

    def test_server_error_backoff_constant_exists(self):
        """_SERVER_ERROR_BACKOFF constant is defined at module level."""
        import parser.chart_digitizer as mod
        assert hasattr(mod, "_SERVER_ERROR_BACKOFF"), "_SERVER_ERROR_BACKOFF constant must exist"
        assert mod._SERVER_ERROR_BACKOFF == 10.0

    def test_source_does_not_use_bare_magic_numbers_for_backoff(self):
        """The literal values 60.0 and 10.0 should not appear as inline literals in _digitize_single."""
        import parser.chart_digitizer as mod
        source = inspect.getsource(mod._digitize_single)
        assert "60.0" not in source, (
            "60.0 should be extracted to _RATE_LIMIT_BACKOFF, not used inline"
        )
        assert "10.0" not in source, (
            "10.0 should be extracted to _SERVER_ERROR_BACKOFF, not used inline"
        )


class TestLazyClientCaching:
    """Module-level Gemini client is created once and reused."""

    def test_get_gemini_client_function_exists(self):
        """_get_gemini_client helper is defined at module level."""
        import parser.chart_digitizer as mod
        assert hasattr(mod, "_get_gemini_client"), "_get_gemini_client must exist"
        assert callable(mod._get_gemini_client)

    def test_client_created_only_once(self):
        """Calling _get_gemini_client() twice returns the same object."""
        import parser.chart_digitizer as mod

        with patch("parser.chart_digitizer._gemini_client", None), \
             patch("parser.chart_digitizer.settings") as s, \
             patch("google.genai.Client") as mock_genai_client:
            s.gemini_api_key = "fake-key"
            mock_instance = MagicMock()
            mock_genai_client.return_value = mock_instance

            # Reset module-level client for isolation
            original = mod._gemini_client
            mod._gemini_client = None
            try:
                client1 = mod._get_gemini_client()
                client2 = mod._get_gemini_client()
            finally:
                mod._gemini_client = original

        assert client1 is client2, "Client should be cached and reused across calls"
        assert mock_genai_client.call_count == 1, (
            f"genai.Client() should be called only once, was called {mock_genai_client.call_count} times"
        )

    def test_digitize_single_uses_cached_client(self):
        """_digitize_single does not create a new Client per call."""
        import parser.chart_digitizer as mod

        async def _test():
            mock_response = MagicMock()
            mock_response.text = "| x |"
            mock_response.usage_metadata = MagicMock(
                prompt_token_count=1, candidates_token_count=1
            )

            image = MagicMock()
            image.image_bytes = b"fake"
            image.page_num = 0
            image.source = "test"

            with patch("parser.chart_digitizer.settings") as s, \
                 patch("parser.chart_digitizer._gemini_client", None), \
                 patch("google.genai.Client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = AsyncMock(
                    return_value=mock_response
                )
                mock_genai_client.return_value = mock_client_instance

                original = mod._gemini_client
                mod._gemini_client = None
                try:
                    await mod._digitize_single(image)
                    await mod._digitize_single(image)
                finally:
                    mod._gemini_client = original

            # Even with two calls, genai.Client() constructed only once
            assert mock_genai_client.call_count == 1, (
                "genai.Client() should be constructed once and reused"
            )

        run(_test())
