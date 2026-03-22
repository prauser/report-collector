"""Tests for agent/chat_handler.py."""
from __future__ import annotations

import json
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.chat_handler import (
    AnthropicChatProvider,
    LLMChatProvider,
    format_sse_chunk,
    format_sse_done,
    format_sse_error,
    get_default_provider,
    stream_to_sse,
)


# ──────────────────────────────────────────────
# SSE 포맷 유틸리티
# ──────────────────────────────────────────────

class TestFormatSSE:
    def test_sse_chunk_format(self):
        """SSE chunk 포맷 검증: data: {...}\\n\\n."""
        result = format_sse_chunk("안녕하세요")
        assert result.startswith("data: ")
        assert result.endswith("\n\n")
        payload = json.loads(result[6:])
        assert payload["type"] == "text"
        assert payload["text"] == "안녕하세요"

    def test_sse_done_format(self):
        """SSE done 포맷 검증."""
        result = format_sse_done()
        assert result.startswith("data: ")
        assert result.endswith("\n\n")
        payload = json.loads(result[6:])
        assert payload["type"] == "done"

    def test_sse_error_format(self):
        """SSE error 포맷 검증."""
        result = format_sse_error("오류 발생")
        assert result.startswith("data: ")
        assert result.endswith("\n\n")
        payload = json.loads(result[6:])
        assert payload["type"] == "error"
        assert payload["message"] == "오류 발생"

    def test_sse_chunk_korean_preserved(self):
        """한글 문자 ensure_ascii=False 처리."""
        result = format_sse_chunk("삼성전자 분석")
        # 한글이 이스케이프되지 않아야 함
        assert "삼성전자" in result

    def test_sse_chunk_special_chars(self):
        """특수문자 JSON 인코딩 처리."""
        result = format_sse_chunk('{"key": "value"}')
        payload = json.loads(result[6:])
        assert payload["text"] == '{"key": "value"}'


# ──────────────────────────────────────────────
# LLMChatProvider Protocol
# ──────────────────────────────────────────────

class TestLLMChatProviderProtocol:
    def test_anthropic_provider_satisfies_protocol(self):
        """AnthropicChatProvider가 LLMChatProvider Protocol을 충족."""
        # Protocol은 runtime_checkable이므로 isinstance 체크 가능
        provider = MagicMock(spec=AnthropicChatProvider)
        # AnthropicChatProvider has stream_chat method
        assert hasattr(AnthropicChatProvider, "stream_chat")

    def test_custom_provider_satisfies_protocol(self):
        """커스텀 구현체가 Protocol을 구현하면 isinstance 통과."""

        class CustomProvider:
            async def stream_chat(
                self,
                messages,
                model,
                system=None,
                max_tokens=4096,
            ) -> AsyncIterator[str]:
                async def gen():
                    yield "test"
                return gen()

        provider = CustomProvider()
        assert isinstance(provider, LLMChatProvider)

    def test_missing_stream_chat_fails_protocol(self):
        """stream_chat 없는 클래스는 Protocol 미충족."""

        class BadProvider:
            pass

        provider = BadProvider()
        assert not isinstance(provider, LLMChatProvider)


# ──────────────────────────────────────────────
# AnthropicChatProvider (mock 기반)
# ──────────────────────────────────────────────

class TestAnthropicChatProvider:
    @pytest.mark.asyncio
    async def test_stream_chat_yields_text_chunks(self):
        """stream_chat이 텍스트 청크를 yield."""
        provider = AnthropicChatProvider(api_key="test-key")

        # Anthropic client mock
        mock_final_msg = MagicMock()
        mock_final_msg.usage.input_tokens = 100
        mock_final_msg.usage.output_tokens = 50

        async def mock_text_stream():
            for chunk in ["안녕", "하세요", "!"]:
                yield chunk

        mock_stream = AsyncMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.text_stream = mock_text_stream()
        mock_stream.get_final_message = AsyncMock(return_value=mock_final_msg)

        provider._client = MagicMock()
        provider._client.messages.stream = MagicMock(return_value=mock_stream)

        chunks = []
        with patch("agent.chat_handler.record_llm_usage", new_callable=AsyncMock):
            async for chunk in provider.stream_chat(
                messages=[{"role": "user", "content": "안녕"}],
                model="claude-sonnet-4-6",
            ):
                chunks.append(chunk)

        assert chunks == ["안녕", "하세요", "!"]

    @pytest.mark.asyncio
    async def test_stream_chat_records_usage(self):
        """stream_chat 완료 후 record_llm_usage 호출."""
        provider = AnthropicChatProvider(api_key="test-key")

        mock_final_msg = MagicMock()
        mock_final_msg.usage.input_tokens = 200
        mock_final_msg.usage.output_tokens = 100

        async def mock_text_stream():
            yield "응답"

        mock_stream = AsyncMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.text_stream = mock_text_stream()
        mock_stream.get_final_message = AsyncMock(return_value=mock_final_msg)

        provider._client = MagicMock()
        provider._client.messages.stream = MagicMock(return_value=mock_stream)

        with patch("agent.chat_handler.record_llm_usage", new_callable=AsyncMock) as mock_record:
            async for _ in provider.stream_chat(
                messages=[{"role": "user", "content": "테스트"}],
                model="claude-sonnet-4-6",
            ):
                pass

        mock_record.assert_called_once_with(
            model="claude-sonnet-4-6",
            purpose="agent_chat",
            input_tokens=200,
            output_tokens=100,
        )

    @pytest.mark.asyncio
    async def test_stream_chat_passes_system_prompt(self):
        """system 파라미터가 Anthropic API 호출에 전달됨."""
        provider = AnthropicChatProvider(api_key="test-key")

        mock_final_msg = MagicMock()
        mock_final_msg.usage.input_tokens = 50
        mock_final_msg.usage.output_tokens = 20

        async def mock_text_stream():
            yield "ok"

        mock_stream = AsyncMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.text_stream = mock_text_stream()
        mock_stream.get_final_message = AsyncMock(return_value=mock_final_msg)

        provider._client = MagicMock()
        provider._client.messages.stream = MagicMock(return_value=mock_stream)

        with patch("agent.chat_handler.record_llm_usage", new_callable=AsyncMock):
            async for _ in provider.stream_chat(
                messages=[{"role": "user", "content": "질문"}],
                model="claude-sonnet-4-6",
                system="시스템 프롬프트",
            ):
                pass

        call_kwargs = provider._client.messages.stream.call_args.kwargs
        assert call_kwargs.get("system") == "시스템 프롬프트"

    @pytest.mark.asyncio
    async def test_stream_chat_no_system_when_none(self):
        """system=None이면 kwargs에 system 키 없음."""
        provider = AnthropicChatProvider(api_key="test-key")

        mock_final_msg = MagicMock()
        mock_final_msg.usage.input_tokens = 50
        mock_final_msg.usage.output_tokens = 20

        async def mock_text_stream():
            yield "ok"

        mock_stream = AsyncMock()
        mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
        mock_stream.__aexit__ = AsyncMock(return_value=None)
        mock_stream.text_stream = mock_text_stream()
        mock_stream.get_final_message = AsyncMock(return_value=mock_final_msg)

        provider._client = MagicMock()
        provider._client.messages.stream = MagicMock(return_value=mock_stream)

        with patch("agent.chat_handler.record_llm_usage", new_callable=AsyncMock):
            async for _ in provider.stream_chat(
                messages=[{"role": "user", "content": "질문"}],
                model="claude-sonnet-4-6",
                system=None,
            ):
                pass

        call_kwargs = provider._client.messages.stream.call_args.kwargs
        assert "system" not in call_kwargs


# ──────────────────────────────────────────────
# stream_to_sse
# ──────────────────────────────────────────────

class TestStreamToSSE:
    def _make_provider(self, chunks: list[str]) -> LLMChatProvider:
        """주어진 청크를 yield하는 mock provider."""

        async def _stream_chat(messages, model, system=None, max_tokens=4096):
            for chunk in chunks:
                yield chunk

        provider = MagicMock()
        provider.stream_chat = _stream_chat
        return provider

    @pytest.mark.asyncio
    async def test_yields_sse_chunks_and_done(self):
        """청크 + done 이벤트 순서 검증."""
        provider = self._make_provider(["안녕", "하세요"])

        sse_events = []
        async for event in stream_to_sse(
            provider=provider,
            messages=[{"role": "user", "content": "test"}],
            model="claude-sonnet-4-6",
        ):
            sse_events.append(event)

        assert len(sse_events) == 3  # 2 chunks + done
        # 마지막은 done
        last_payload = json.loads(sse_events[-1][6:])
        assert last_payload["type"] == "done"
        # 중간은 text
        payload0 = json.loads(sse_events[0][6:])
        assert payload0["type"] == "text"
        assert payload0["text"] == "안녕"

    @pytest.mark.asyncio
    async def test_yields_sse_error_on_exception(self):
        """provider 예외 발생 시 error SSE 이벤트 반환."""

        async def _failing_stream(messages, model, system=None, max_tokens=4096):
            raise RuntimeError("API 오류")
            yield  # make it a generator

        provider = MagicMock()
        provider.stream_chat = _failing_stream

        sse_events = []
        async for event in stream_to_sse(
            provider=provider,
            messages=[{"role": "user", "content": "test"}],
            model="claude-sonnet-4-6",
        ):
            sse_events.append(event)

        assert len(sse_events) == 2
        payload = json.loads(sse_events[0][6:])
        assert payload["type"] == "error"
        assert "API 오류" in payload["message"]
        # done event follows error so client doesn't hang
        done_payload = json.loads(sse_events[1][6:])
        assert done_payload["type"] == "done"

    @pytest.mark.asyncio
    async def test_empty_chunks_skipped(self):
        """빈 문자열 청크는 SSE 이벤트로 변환되지 않음."""
        provider = self._make_provider(["", "내용", ""])

        sse_events = []
        async for event in stream_to_sse(
            provider=provider,
            messages=[{"role": "user", "content": "test"}],
            model="claude-sonnet-4-6",
        ):
            sse_events.append(event)

        # 빈 청크 제외 → "내용" 1개 + done 1개
        assert len(sse_events) == 2
        payload = json.loads(sse_events[0][6:])
        assert payload["text"] == "내용"

    @pytest.mark.asyncio
    async def test_sse_format_each_event(self):
        """모든 SSE 이벤트가 올바른 형식."""
        provider = self._make_provider(["A", "B", "C"])

        async for event in stream_to_sse(
            provider=provider,
            messages=[],
            model="test-model",
        ):
            assert event.startswith("data: ")
            assert event.endswith("\n\n")
            # JSON 파싱 가능
            json.loads(event[6:])


# ──────────────────────────────────────────────
# get_default_provider
# ──────────────────────────────────────────────

class TestGetDefaultProvider:
    def test_returns_anthropic_provider(self):
        """get_default_provider()가 AnthropicChatProvider 반환."""
        with patch("agent.chat_handler.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.llm_timeout = 30
            provider = get_default_provider()
        assert isinstance(provider, AnthropicChatProvider)

    def test_provider_satisfies_protocol(self):
        """반환된 provider가 LLMChatProvider Protocol 충족."""
        with patch("agent.chat_handler.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.llm_timeout = 30
            provider = get_default_provider()
        assert isinstance(provider, LLMChatProvider)
