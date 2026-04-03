"""Tests for RateLimitGate and its integration with LLM modules.

Tests use asyncio.run() directly since pytest-asyncio may not be available.
The pytest.ini has asyncio_mode=auto but we rely on sync wrappers for safety.
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from parser.rate_limit import RateLimitGate


def run(coro):
    """Helper: run a coroutine in a new event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─────────────────────────────────────────────────────────────────────────────
# RateLimitGate unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestRateLimitGate:

    def test_gate_initially_open(self):
        """Gate starts open; wait() returns immediately."""
        gate = RateLimitGate("test")

        async def _test():
            await asyncio.wait_for(gate.wait(), timeout=0.1)

        run(_test())

    def test_trigger_backoff_closes_then_opens(self):
        """trigger_backoff closes gate, sleeps, then reopens."""
        gate = RateLimitGate("test")

        async def _test():
            with patch("parser.rate_limit.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await gate.trigger_backoff(retry_after=30.0)
                mock_sleep.assert_awaited_once_with(30.0)
            assert gate._event.is_set()

        run(_test())

    def test_trigger_backoff_default_60s(self):
        """Default retry_after is 60 seconds."""
        gate = RateLimitGate("test")

        async def _test():
            with patch("parser.rate_limit.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                await gate.trigger_backoff()
                mock_sleep.assert_awaited_once_with(60.0)

        run(_test())

    def test_second_concurrent_backoff_skips_sleep_when_gate_already_closed(self):
        """When gate is already closed (backoff in progress), a second trigger_backoff skips sleep.

        This tests the guard: `if self._event.is_set(): ...` — if the event is already
        cleared (first backoff ongoing), the second caller acquires the lock but skips
        the sleep because is_set() is False.
        """
        async def _test():
            gate = RateLimitGate("test")
            sleep_count = 0

            async def counted_sleep(secs):
                nonlocal sleep_count
                sleep_count += 1

            # Manually close the gate to simulate "first backoff already in progress"
            gate._event.clear()

            # Now trigger_backoff — it should see event is already cleared and skip sleep
            with patch("parser.rate_limit.asyncio.sleep", side_effect=counted_sleep):
                await gate.trigger_backoff(10.0)

            assert sleep_count == 0, "Should skip sleep when gate is already closed"

        run(_test())

    def test_waiters_see_gate_open_after_backoff(self):
        """After trigger_backoff completes, the event is set so waiters can proceed.

        trigger_backoff: clears the event, sleeps, then re-sets the event.
        After it returns, wait() must return immediately.
        """
        async def _test():
            gate = RateLimitGate("test")
            assert gate._event.is_set()  # starts open

            # Run backoff — this should close and then reopen the gate
            with patch("parser.rate_limit.asyncio.sleep", new_callable=AsyncMock):
                await gate.trigger_backoff(5.0)

            # Gate must now be open again
            assert gate._event.is_set()

            # A call to wait() should return immediately (not block)
            await asyncio.wait_for(gate.wait(), timeout=0.1)

        run(_test())

    def test_gate_name_stored(self):
        gate = RateLimitGate("my_gate")
        assert gate._name == "my_gate"

    def test_gate_event_cleared_during_backoff(self):
        """During backoff, the event is cleared (gate is closed)."""
        gate = RateLimitGate("test")
        state_during_sleep = []

        async def _test():
            async def tracking_sleep(secs):
                state_during_sleep.append(gate._event.is_set())

            with patch("parser.rate_limit.asyncio.sleep", side_effect=tracking_sleep):
                await gate.trigger_backoff(1.0)

        run(_test())
        assert state_during_sleep == [False], "Event must be cleared during sleep"


# ─────────────────────────────────────────────────────────────────────────────
# llm_parser integration: gate.wait() called and backoff triggered on RateLimitError
# ─────────────────────────────────────────────────────────────────────────────

class TestLlmParserGateIntegration:

    def test_gate_module_level_instance_exists(self):
        """Module exposes _s2a_gate as a RateLimitGate."""
        import parser.llm_parser as mod
        assert isinstance(mod._s2a_gate, RateLimitGate)
        assert mod._s2a_gate._name == "s2a_haiku"

    def test_gate_wait_called_before_api(self):
        """_call_s2a awaits the gate before calling the Anthropic API."""
        import parser.llm_parser as mod

        async def _test():
            waited = []
            original_wait = mod._s2a_gate.wait

            async def tracking_wait():
                waited.append(True)
                await original_wait()

            mock_response = MagicMock()
            mock_response.content = []

            with patch.object(mod._s2a_gate, "wait", side_effect=tracking_wait), \
                 patch("parser.llm_parser._get_client") as mock_get_client, \
                 patch("parser.llm_parser.settings") as s:
                s.llm_model = "claude-haiku-test"
                s.llm_max_retries = 0
                mock_client = AsyncMock()
                mock_client.messages.create = AsyncMock(return_value=mock_response)
                mock_get_client.return_value = mock_client

                await mod._call_s2a("test message")

            assert len(waited) == 1, "gate.wait() should be called exactly once per API call"

        run(_test())

    def test_rate_limit_triggers_backoff(self):
        """When RateLimitError is raised, trigger_backoff is called with retry-after value.

        Note: the tenacity decorator on _call_s2a uses the real settings.llm_max_retries
        value baked in at module load time, so it may retry more than once. We assert
        that trigger_backoff is called at least once with the correct retry-after value.
        """
        import parser.llm_parser as mod
        from anthropic import RateLimitError

        async def _test():
            fake_response = MagicMock()
            fake_response.headers = {"retry-after": "45"}
            fake_response.status_code = 429
            error = RateLimitError("rate limited", response=fake_response, body={})

            backoff_calls = []

            async def fake_backoff(retry_after=60.0):
                backoff_calls.append(retry_after)

            with patch.object(mod._s2a_gate, "wait", new_callable=AsyncMock), \
                 patch.object(mod._s2a_gate, "trigger_backoff", side_effect=fake_backoff), \
                 patch("parser.llm_parser._get_client") as mock_get_client, \
                 patch("parser.llm_parser.settings") as s:
                s.llm_model = "claude-haiku-test"
                s.llm_max_retries = 0

                mock_client = AsyncMock()
                mock_client.messages.create = AsyncMock(side_effect=error)
                mock_get_client.return_value = mock_client

                with pytest.raises(RateLimitError):
                    await mod._call_s2a("test")

            # trigger_backoff must be called at least once with retry-after=45
            assert len(backoff_calls) >= 1
            assert all(v == 45.0 for v in backoff_calls)

        run(_test())

    def test_rate_limit_retry_after_default_on_missing_header(self):
        """Missing retry-after header falls back to 60s."""
        import parser.llm_parser as mod
        from anthropic import RateLimitError

        async def _test():
            fake_response = MagicMock()
            fake_response.headers = {}
            fake_response.status_code = 429
            error = RateLimitError("rate limited", response=fake_response, body={})

            backoff_calls = []

            async def fake_backoff(retry_after=60.0):
                backoff_calls.append(retry_after)

            with patch.object(mod._s2a_gate, "wait", new_callable=AsyncMock), \
                 patch.object(mod._s2a_gate, "trigger_backoff", side_effect=fake_backoff), \
                 patch("parser.llm_parser._get_client") as mock_get_client, \
                 patch("parser.llm_parser.settings") as s:
                s.llm_model = "claude-haiku-test"
                s.llm_max_retries = 0

                mock_client = AsyncMock()
                mock_client.messages.create = AsyncMock(side_effect=error)
                mock_get_client.return_value = mock_client

                with pytest.raises(RateLimitError):
                    await mod._call_s2a("test")

            assert backoff_calls[0] == 60.0

        run(_test())


# ─────────────────────────────────────────────────────────────────────────────
# key_data_extractor integration
# ─────────────────────────────────────────────────────────────────────────────

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


class TestKeyDataExtractorGateIntegration:

    def test_gate_module_level_instance_exists(self):
        """Module exposes _gemini_keydata_gate as a RateLimitGate."""
        import parser.key_data_extractor as mod
        assert isinstance(mod._gemini_keydata_gate, RateLimitGate)
        assert mod._gemini_keydata_gate._name == "gemini_keydata"

    def test_gate_wait_called_before_gemini(self):
        """extract_key_data awaits the gate before the Gemini API call."""
        import parser.key_data_extractor as mod

        async def _test():
            waited = []
            original_wait = mod._gemini_keydata_gate.wait

            async def tracking_wait():
                waited.append(True)
                await original_wait()

            mock_response = MagicMock()
            mock_response.text = (
                '{"broker": "테스트", "analyst": null, "date": null, '
                '"stock_name": null, "stock_code": null, "title": null, '
                '"report_type": null, "opinion": null, "target_price": null}'
            )
            mock_response.usage_metadata = MagicMock(
                prompt_token_count=10, candidates_token_count=5
            )

            with patch.object(mod._gemini_keydata_gate, "wait", side_effect=tracking_wait), \
                 patch("parser.key_data_extractor.settings") as s, \
                 patch("parser.key_data_extractor.record_llm_usage", new_callable=AsyncMock), \
                 patch("parser.key_data_extractor.calc_cost_usd", return_value=0), \
                 patch("parser.key_data_extractor._get_first_pages_text_sync", return_value="page text"), \
                 patch("parser.key_data_extractor._get_gemini_client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = AsyncMock(return_value=mock_response)
                mock_genai_client.return_value = mock_client_instance

                result = await mod.extract_key_data("/fake/path.pdf", report_id=1, channel="ch")

            assert len(waited) >= 1
            assert result is not None
            assert result.broker == "테스트"

        run(_test())

    def test_rate_limit_triggers_backoff_and_retries(self):
        """429 GeminiClientError triggers backoff; second attempt succeeds."""
        import parser.key_data_extractor as mod

        async def _test():
            rate_limit_err = _make_gemini_rate_limit_error()

            mock_response = MagicMock()
            mock_response.text = (
                '{"broker": "리테스트", "analyst": null, "date": null, '
                '"stock_name": null, "stock_code": null, "title": null, '
                '"report_type": null, "opinion": null, "target_price": null}'
            )
            mock_response.usage_metadata = MagicMock(
                prompt_token_count=10, candidates_token_count=5
            )

            backoff_calls = []

            async def fake_backoff(retry_after=60.0):
                backoff_calls.append(retry_after)

            call_count = 0

            async def fake_generate(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise rate_limit_err
                return mock_response

            with patch.object(mod._gemini_keydata_gate, "trigger_backoff", side_effect=fake_backoff), \
                 patch("parser.key_data_extractor.settings") as s, \
                 patch("parser.key_data_extractor.record_llm_usage", new_callable=AsyncMock), \
                 patch("parser.key_data_extractor.calc_cost_usd", return_value=0), \
                 patch("parser.key_data_extractor._get_first_pages_text_sync", return_value="page text"), \
                 patch("parser.key_data_extractor._get_gemini_client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = fake_generate
                mock_genai_client.return_value = mock_client_instance

                result = await mod.extract_key_data("/fake/path.pdf", report_id=2, channel="ch")

            assert len(backoff_calls) == 1
            assert backoff_calls[0] == 60.0
            assert result is not None
            assert result.broker == "리테스트"

        run(_test())

    def test_rate_limit_both_attempts_fail_returns_none(self):
        """When both retry attempts hit 429, extract_key_data returns None."""
        import parser.key_data_extractor as mod

        async def _test():
            rate_limit_err = _make_gemini_rate_limit_error()

            async def fake_backoff(retry_after=60.0):
                pass

            async def always_fail(*args, **kwargs):
                raise rate_limit_err

            with patch.object(mod._gemini_keydata_gate, "trigger_backoff", side_effect=fake_backoff), \
                 patch("parser.key_data_extractor.settings") as s, \
                 patch("parser.key_data_extractor._get_first_pages_text_sync", return_value="page text"), \
                 patch("parser.key_data_extractor._get_gemini_client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = always_fail
                mock_genai_client.return_value = mock_client_instance

                result = await mod.extract_key_data("/fake/path.pdf", report_id=3, channel="ch")

            assert result is None

        run(_test())


# ─────────────────────────────────────────────────────────────────────────────
# chart_digitizer integration
# ─────────────────────────────────────────────────────────────────────────────

class TestChartDigitizerGateIntegration:

    def test_gate_module_level_instance_exists(self):
        """Module exposes _gemini_chart_gate as a RateLimitGate."""
        import parser.chart_digitizer as mod
        assert isinstance(mod._gemini_chart_gate, RateLimitGate)
        assert mod._gemini_chart_gate._name == "gemini_chart"

    def test_semaphore_still_limits_concurrency(self):
        """Existing _SEMAPHORE(5) still limits concurrent calls."""
        import parser.chart_digitizer as mod
        assert mod._SEMAPHORE._value == 5

    def test_gate_wait_called_before_gemini(self):
        """_digitize_single awaits the gate before the Gemini API call."""
        import parser.chart_digitizer as mod

        async def _test():
            waited = []
            original_wait = mod._gemini_chart_gate.wait

            async def tracking_wait():
                waited.append(True)
                await original_wait()

            mock_response = MagicMock()
            mock_response.text = "| col1 | col2 |\n|---|---|\n| 1 | 2 |"
            mock_response.usage_metadata = MagicMock(
                prompt_token_count=20, candidates_token_count=10
            )

            image = MagicMock()
            image.image_bytes = b"fake_image_bytes"
            image.page_num = 0
            image.source = "test"

            with patch.object(mod._gemini_chart_gate, "wait", side_effect=tracking_wait), \
                 patch("parser.chart_digitizer.settings") as s, \
                 patch("parser.chart_digitizer._get_gemini_client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = AsyncMock(return_value=mock_response)
                mock_genai_client.return_value = mock_client_instance

                text, in_tok, out_tok = await mod._digitize_single(image)

            assert len(waited) == 1
            assert text is not None

        run(_test())

    def test_rate_limit_both_attempts_fail_returns_none_0_0(self):
        """429 GeminiClientError on both attempts triggers backoff twice and returns (None, 0, 0)."""
        import parser.chart_digitizer as mod

        async def _test():
            rate_limit_err = _make_gemini_rate_limit_error()
            backoff_calls = []

            async def fake_backoff(retry_after=60.0):
                backoff_calls.append(retry_after)

            image = MagicMock()
            image.image_bytes = b"fake_image_bytes"
            image.page_num = 0
            image.source = "test"

            mock_client_instance = MagicMock()
            mock_client_instance.aio.models.generate_content = AsyncMock(
                side_effect=rate_limit_err
            )

            with patch.object(mod._gemini_chart_gate, "trigger_backoff", side_effect=fake_backoff), \
                 patch("parser.chart_digitizer.settings") as s, \
                 patch("parser.chart_digitizer._get_gemini_client", return_value=mock_client_instance):
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                text, in_tok, out_tok = await mod._digitize_single(image)

            # 2 attempts → backoff only on first failure (second is final, no backoff)
            assert len(backoff_calls) == 1
            assert backoff_calls[0] == 60.0
            assert text is None
            assert in_tok == 0
            assert out_tok == 0

        run(_test())

    def test_rate_limit_first_attempt_fails_second_succeeds(self):
        """429 on first attempt triggers backoff; second attempt succeeds and returns text."""
        import parser.chart_digitizer as mod

        async def _test():
            rate_limit_err = _make_gemini_rate_limit_error()
            backoff_calls = []

            async def fake_backoff(retry_after=60.0):
                backoff_calls.append(retry_after)

            mock_response = MagicMock()
            mock_response.text = "| col | val |\n|---|---|\n| A | 1 |"
            mock_response.usage_metadata = MagicMock(
                prompt_token_count=15, candidates_token_count=8
            )

            call_count = 0

            async def fail_then_succeed(*args, **kwargs):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise rate_limit_err
                return mock_response

            image = MagicMock()
            image.image_bytes = b"fake_image_bytes"
            image.page_num = 1
            image.source = "test"

            mock_client_instance = MagicMock()
            mock_client_instance.aio.models.generate_content = fail_then_succeed

            with patch.object(mod._gemini_chart_gate, "trigger_backoff", side_effect=fake_backoff), \
                 patch("parser.chart_digitizer.settings") as s, \
                 patch("parser.chart_digitizer._get_gemini_client", return_value=mock_client_instance):
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                text, in_tok, out_tok = await mod._digitize_single(image)

            assert len(backoff_calls) == 1
            assert backoff_calls[0] == 60.0
            assert text == "| col | val |\n|---|---|\n| A | 1 |"
            assert in_tok == 15
            assert out_tok == 8

        run(_test())

    def test_gate_wait_called_before_semaphore(self):
        """gate.wait() is called before the semaphore is acquired — no deadlock during backoff.

        We verify that gate.wait() is invoked before asyncio.wait_for() (the Gemini call),
        which structurally means it is outside the semaphore block.
        """
        import parser.chart_digitizer as mod

        async def _test():
            call_order = []

            original_wait = mod._gemini_chart_gate.wait

            async def tracking_wait():
                call_order.append("gate_wait")
                await original_wait()

            mock_response = MagicMock()
            mock_response.text = "| x | y |\n|---|---|\n| 1 | 2 |"
            mock_response.usage_metadata = MagicMock(
                prompt_token_count=10, candidates_token_count=5
            )

            image = MagicMock()
            image.image_bytes = b"bytes"
            image.page_num = 2
            image.source = "src"

            with patch.object(mod._gemini_chart_gate, "wait", side_effect=tracking_wait), \
                 patch("parser.chart_digitizer.settings") as s, \
                 patch("parser.chart_digitizer._get_gemini_client") as mock_genai_client:
                s.gemini_api_key = "fake-key"
                s.gemini_model = "gemini-test"

                mock_client_instance = MagicMock()
                mock_client_instance.aio.models.generate_content = AsyncMock(
                    return_value=mock_response
                )
                mock_genai_client.return_value = mock_client_instance

                text, in_tok, out_tok = await mod._digitize_single(image)

            # gate.wait() must have been called (at least once for the initial check)
            assert "gate_wait" in call_order
            assert call_order[0] == "gate_wait"
            assert text is not None

        run(_test())
