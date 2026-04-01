"""Tests for agent/chat_handler.py."""
from __future__ import annotations

import json
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import agent.chat_handler as _chat_handler_module
from agent.chat_handler import (
    AnthropicChatProvider,
    LLMChatProvider,
    _make_tool_summary,
    format_sse_chunk,
    format_sse_done,
    format_sse_error,
    format_sse_thinking,
    format_sse_tool_call,
    format_sse_tool_result,
    get_default_provider,
    stream_agent_response,
    stream_to_sse,
)


@pytest.fixture(autouse=True)
def reset_agent_client():
    """Reset the module-level _agent_client singleton before each test."""
    _chat_handler_module._agent_client = None
    yield
    _chat_handler_module._agent_client = None


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


# ──────────────────────────────────────────────
# New SSE formatters (tool-use)
# ──────────────────────────────────────────────

class TestNewSseFormatters:
    def test_format_sse_thinking(self):
        result = format_sse_thinking("thinking about it")
        assert result.startswith("data: ")
        assert result.endswith("\n\n")
        payload = json.loads(result[6:])
        assert payload["type"] == "thinking"
        assert payload["text"] == "thinking about it"

    def test_format_sse_tool_call_keys(self):
        result = format_sse_tool_call("toolu_abc", "search_reports", {"limit": 5})
        payload = json.loads(result[6:])
        assert payload["type"] == "tool_call"
        assert payload["id"] == "toolu_abc"
        assert payload["name"] == "search_reports"
        assert payload["input"] == {"limit": 5}

    def test_format_sse_tool_result_keys(self):
        result = format_sse_tool_result("toolu_xyz", "list_stocks", "10개 종목")
        payload = json.loads(result[6:])
        assert payload["type"] == "tool_result"
        assert payload["id"] == "toolu_xyz"
        assert payload["name"] == "list_stocks"
        assert payload["summary"] == "10개 종목"

    def test_korean_chars_not_escaped_in_tool_call(self):
        result = format_sse_tool_call("id", "search_reports", {"stock_name": "삼성전자"})
        assert "삼성전자" in result

    def test_korean_chars_not_escaped_in_tool_result(self):
        result = format_sse_tool_result("id", "get_report_stats", "기간 2024-01-01~2024-12-31 통계")
        assert "기간" in result

    def test_format_sse_thinking_korean(self):
        result = format_sse_thinking("이 질문은 삼성전자에 관한 것이군요.")
        assert "삼성전자" in result


# ──────────────────────────────────────────────
# _make_tool_summary
# ──────────────────────────────────────────────

class TestMakeToolSummary:
    def test_search_reports_with_total_count(self):
        result = _make_tool_summary("search_reports", {"reports": [], "total_count": 42})
        assert "42건" in result

    def test_search_reports_fallback_to_reports_len(self):
        reports = [{"report_id": i} for i in range(3)]
        result = _make_tool_summary("search_reports", {"reports": reports})
        assert "3건" in result

    def test_get_report_detail(self):
        reports = [{"report_id": 1}, {"report_id": 2}]
        result = _make_tool_summary("get_report_detail", {"reports": reports})
        assert "2건" in result

    def test_list_stocks_with_total_count(self):
        result = _make_tool_summary("list_stocks", {"stocks": [], "total_count": 15})
        assert "15개" in result

    def test_list_stocks_fallback_to_stocks_len(self):
        stocks = [{"stock_name": f"종목{i}"} for i in range(7)]
        result = _make_tool_summary("list_stocks", {"stocks": stocks})
        assert "7개" in result

    def test_get_report_stats(self):
        result = _make_tool_summary(
            "get_report_stats",
            {"period": {"from": "2024-01-01", "to": "2024-03-31"}, "total_reports": 100},
        )
        assert "2024-01-01" in result
        assert "2024-03-31" in result

    def test_error_key_present(self):
        result = _make_tool_summary("search_reports", {"error": "DB 연결 실패"})
        assert "오류" in result
        assert "DB 연결 실패" in result

    def test_unknown_tool_name_returns_string(self):
        result = _make_tool_summary("mystery_tool", {"foo": "bar"})
        assert isinstance(result, str)
        assert len(result) > 0

    def test_get_report_detail_empty_reports(self):
        result = _make_tool_summary("get_report_detail", {"reports": []})
        assert "0건" in result


# ──────────────────────────────────────────────
# stream_agent_response
# ──────────────────────────────────────────────

def _make_text_block(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    return block


def _make_tool_use_block(tool_id: str, name: str, input_dict: dict):
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = name
    block.input = input_dict
    return block


def _make_api_response(content: list, stop_reason: str = "end_turn",
                       input_tokens: int = 100, output_tokens: int = 50):
    resp = MagicMock()
    resp.content = content
    resp.stop_reason = stop_reason
    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens
    resp.usage = usage
    return resp


async def _collect_events(gen) -> list[dict]:
    events = []
    async for event in gen:
        if event.startswith("data: "):
            payload = json.loads(event[6:])
            events.append(payload)
    return events


class TestStreamAgentResponse:
    @pytest.mark.asyncio
    async def test_simple_end_turn_text_and_done(self):
        db_session = AsyncMock()
        response = _make_api_response(
            content=[_make_text_block("안녕하세요!")],
            stop_reason="end_turn",
        )
        with patch("agent.chat_handler.AsyncAnthropic") as MockClient, \
             patch("agent.chat_handler.record_llm_usage", new=AsyncMock()):
            mock_client = MockClient.return_value
            mock_client.messages.create = AsyncMock(return_value=response)
            events = await _collect_events(stream_agent_response(
                messages=[{"role": "user", "content": "hello"}],
                model="m", system="sys", tools=[], db_session=db_session,
            ))
        types = [e["type"] for e in events]
        assert "text" in types
        assert "done" in types
        text_event = next(e for e in events if e["type"] == "text")
        assert text_event["text"] == "안녕하세요!"

    @pytest.mark.asyncio
    async def test_empty_text_block_not_yielded(self):
        db_session = AsyncMock()
        response = _make_api_response(
            content=[_make_text_block("")],
            stop_reason="end_turn",
        )
        with patch("agent.chat_handler.AsyncAnthropic") as MockClient, \
             patch("agent.chat_handler.record_llm_usage", new=AsyncMock()):
            mock_client = MockClient.return_value
            mock_client.messages.create = AsyncMock(return_value=response)
            events = await _collect_events(stream_agent_response(
                messages=[{"role": "user", "content": "hello"}],
                model="m", system="sys", tools=[], db_session=db_session,
            ))
        types = [e["type"] for e in events]
        assert "text" not in types
        assert "done" in types

    @pytest.mark.asyncio
    async def test_tool_use_loop_emits_thinking_tool_call_result_text_done(self):
        db_session = AsyncMock()
        response_1 = _make_api_response(
            content=[
                _make_text_block("검색해볼게요."),
                _make_tool_use_block("toolu_001", "search_reports", {"stock_name": "삼성"}),
            ],
            stop_reason="tool_use",
        )
        response_2 = _make_api_response(
            content=[_make_text_block("5건 찾았습니다.")],
            stop_reason="end_turn",
        )
        with patch("agent.chat_handler.AsyncAnthropic") as MockClient, \
             patch("agent.chat_handler.execute_tool", new=AsyncMock(
                 return_value={"reports": [], "total_count": 5}
             )), \
             patch("agent.chat_handler.record_llm_usage", new=AsyncMock()):
            mock_client = MockClient.return_value
            mock_client.messages.create = AsyncMock(side_effect=[response_1, response_2])
            events = await _collect_events(stream_agent_response(
                messages=[{"role": "user", "content": "삼성 리포트"}],
                model="m", system="sys", tools=[], db_session=db_session,
            ))
        types = [e["type"] for e in events]
        assert "thinking" in types
        assert "tool_call" in types
        assert "tool_result" in types
        assert "text" in types
        assert "done" in types
        tool_call = next(e for e in events if e["type"] == "tool_call")
        assert tool_call["name"] == "search_reports"
        tool_result = next(e for e in events if e["type"] == "tool_result")
        assert "5건" in tool_result["summary"]

    @pytest.mark.asyncio
    async def test_tool_execution_exception_returns_error_summary(self):
        db_session = AsyncMock()
        response_1 = _make_api_response(
            content=[_make_tool_use_block("t2", "search_reports", {})],
            stop_reason="tool_use",
        )
        response_2 = _make_api_response(
            content=[_make_text_block("오류 발생했습니다.")],
            stop_reason="end_turn",
        )
        with patch("agent.chat_handler.AsyncAnthropic") as MockClient, \
             patch("agent.chat_handler.execute_tool", new=AsyncMock(
                 side_effect=RuntimeError("DB down")
             )), \
             patch("agent.chat_handler.record_llm_usage", new=AsyncMock()):
            mock_client = MockClient.return_value
            mock_client.messages.create = AsyncMock(side_effect=[response_1, response_2])
            events = await _collect_events(stream_agent_response(
                messages=[{"role": "user", "content": "검색"}],
                model="m", system="sys", tools=[], db_session=db_session,
            ))
        tool_result = next((e for e in events if e["type"] == "tool_result"), None)
        assert tool_result is not None
        assert "오류" in tool_result["summary"]

    @pytest.mark.asyncio
    async def test_api_exception_yields_error_and_done(self):
        db_session = AsyncMock()
        with patch("agent.chat_handler.AsyncAnthropic") as MockClient, \
             patch("agent.chat_handler.record_llm_usage", new=AsyncMock()):
            mock_client = MockClient.return_value
            mock_client.messages.create = AsyncMock(side_effect=Exception("rate limit"))
            events = await _collect_events(stream_agent_response(
                messages=[{"role": "user", "content": "hello"}],
                model="m", system="sys", tools=[], db_session=db_session,
            ))
        types = [e["type"] for e in events]
        assert "error" in types
        assert "done" in types
        error_event = next(e for e in events if e["type"] == "error")
        assert "rate limit" in error_event["message"]

    @pytest.mark.asyncio
    async def test_max_iterations_stops_loop(self):
        db_session = AsyncMock()
        tool_response = _make_api_response(
            content=[_make_tool_use_block("t_loop", "search_reports", {})],
            stop_reason="tool_use",
        )
        with patch("agent.chat_handler.AsyncAnthropic") as MockClient, \
             patch("agent.chat_handler.execute_tool", new=AsyncMock(
                 return_value={"reports": [], "total_count": 0}
             )), \
             patch("agent.chat_handler.record_llm_usage", new=AsyncMock()):
            mock_client = MockClient.return_value
            mock_client.messages.create = AsyncMock(return_value=tool_response)
            events = await _collect_events(stream_agent_response(
                messages=[{"role": "user", "content": "loop"}],
                model="m", system="sys", tools=[], db_session=db_session,
                max_iterations=3,
            ))
        types = [e["type"] for e in events]
        assert "done" in types
        assert mock_client.messages.create.call_count == 3

    @pytest.mark.asyncio
    async def test_usage_accumulated_and_recorded(self):
        db_session = AsyncMock()
        response_1 = _make_api_response(
            content=[_make_tool_use_block("t1", "list_stocks", {})],
            stop_reason="tool_use",
            input_tokens=100,
            output_tokens=50,
        )
        response_2 = _make_api_response(
            content=[_make_text_block("결과.")],
            stop_reason="end_turn",
            input_tokens=200,
            output_tokens=80,
        )
        with patch("agent.chat_handler.AsyncAnthropic") as MockClient, \
             patch("agent.chat_handler.execute_tool", new=AsyncMock(
                 return_value={"stocks": [], "total_count": 0}
             )), \
             patch("agent.chat_handler.record_llm_usage", new=AsyncMock()) as mock_record:
            mock_client = MockClient.return_value
            mock_client.messages.create = AsyncMock(side_effect=[response_1, response_2])
            await _collect_events(stream_agent_response(
                messages=[{"role": "user", "content": "종목"}],
                model="m", system="sys", tools=[], db_session=db_session,
            ))
        mock_record.assert_called_once()
        kw = mock_record.call_args.kwargs
        assert kw["input_tokens"] == 300
        assert kw["output_tokens"] == 130
        assert kw["purpose"] == "agent_chat"

    @pytest.mark.asyncio
    async def test_messages_extended_with_assistant_and_tool_result(self):
        db_session = AsyncMock()
        response_1 = _make_api_response(
            content=[_make_tool_use_block("t1", "search_reports", {"stock_name": "SK"})],
            stop_reason="tool_use",
        )
        response_2 = _make_api_response(
            content=[_make_text_block("찾았습니다.")],
            stop_reason="end_turn",
        )
        captured = []

        async def _create(**kwargs):
            captured.append(kwargs["messages"])
            return response_1 if len(captured) == 1 else response_2

        with patch("agent.chat_handler.AsyncAnthropic") as MockClient, \
             patch("agent.chat_handler.execute_tool", new=AsyncMock(
                 return_value={"reports": [], "total_count": 2}
             )), \
             patch("agent.chat_handler.record_llm_usage", new=AsyncMock()):
            mock_client = MockClient.return_value
            mock_client.messages.create = AsyncMock(side_effect=_create)
            await _collect_events(stream_agent_response(
                messages=[{"role": "user", "content": "SK 리포트"}],
                model="m", system="sys", tools=[], db_session=db_session,
            ))

        assert len(captured) == 2
        second_messages = captured[1]
        assert len(second_messages) == 3
        assert second_messages[1]["role"] == "assistant"
        assert second_messages[2]["role"] == "user"
        tr_content = second_messages[2]["content"][0]
        assert tr_content["type"] == "tool_result"
        assert tr_content["tool_use_id"] == "t1"


# ──────────────────────────────────────────────
# TOOL_SYSTEM_PROMPT tests
# ──────────────────────────────────────────────

class TestToolSystemPrompt:
    def test_importable(self):
        from agent.prompt_templates import TOOL_SYSTEM_PROMPT
        assert isinstance(TOOL_SYSTEM_PROMPT, str)
        assert len(TOOL_SYSTEM_PROMPT) > 0

    def test_contains_search_reports_reference(self):
        from agent.prompt_templates import TOOL_SYSTEM_PROMPT
        assert "search_reports" in TOOL_SYSTEM_PROMPT

    def test_contains_korean_response_principle(self):
        from agent.prompt_templates import TOOL_SYSTEM_PROMPT
        assert "한국어" in TOOL_SYSTEM_PROMPT

    def test_existing_system_prompt_unchanged(self):
        from agent.prompt_templates import SYSTEM_PROMPT
        assert isinstance(SYSTEM_PROMPT, str)
        assert "증권 리포트" in SYSTEM_PROMPT

    def test_build_user_prompt_still_works(self):
        from agent.prompt_templates import build_user_prompt
        result = build_user_prompt("테스트 질문", None)
        assert "테스트 질문" in result

    def test_build_user_prompt_with_context(self):
        from agent.prompt_templates import build_user_prompt
        result = build_user_prompt("질문", "컨텍스트 데이터")
        assert "질문" in result
        assert "컨텍스트 데이터" in result


# ──────────────────────────────────────────────
# Fix regression tests (CRITICAL 1-5)
# ──────────────────────────────────────────────

class TestStreamAgentResponseFixes:
    """Regression tests for the 5 critical fixes."""

    # ── CRITICAL 1: record_llm_usage called even on early API error return ──

    @pytest.mark.asyncio
    async def test_usage_recorded_on_api_error(self):
        """record_llm_usage must be called even when API raises (early return path)."""
        db_session = AsyncMock()
        with patch("agent.chat_handler.AsyncAnthropic") as MockClient, \
             patch("agent.chat_handler.record_llm_usage", new_callable=AsyncMock) as mock_record:
            mock_client = MockClient.return_value
            mock_client.messages.create = AsyncMock(side_effect=Exception("network error"))
            events = await _collect_events(stream_agent_response(
                messages=[{"role": "user", "content": "hi"}],
                model="m", system="sys", tools=[], db_session=db_session,
            ))
        # usage must still be recorded despite the early return
        mock_record.assert_called_once()
        types = [e["type"] for e in events]
        assert "error" in types
        assert "done" in types

    # ── CRITICAL 2: iteration defined even when max_iterations=0 ──

    @pytest.mark.asyncio
    async def test_max_iterations_zero_yields_done(self):
        """max_iterations=0 must not raise NameError for 'iteration'."""
        db_session = AsyncMock()
        with patch("agent.chat_handler.AsyncAnthropic"), \
             patch("agent.chat_handler.record_llm_usage", new_callable=AsyncMock):
            events = await _collect_events(stream_agent_response(
                messages=[{"role": "user", "content": "hi"}],
                model="m", system="sys", tools=[], db_session=db_session,
                max_iterations=0,
            ))
        types = [e["type"] for e in events]
        assert "done" in types

    # ── CRITICAL 3: max_tokens stop_reason does not duplicate text ──

    @pytest.mark.asyncio
    async def test_max_tokens_stop_reason_no_duplicate_text(self):
        """When stop_reason is 'max_tokens', text is NOT re-emitted as a second chunk."""
        db_session = AsyncMock()
        # The LLM emits text in the non-final turn (thinking), then hits max_tokens.
        response = _make_api_response(
            content=[_make_text_block("partial answer")],
            stop_reason="max_tokens",
        )
        with patch("agent.chat_handler.AsyncAnthropic") as MockClient, \
             patch("agent.chat_handler.record_llm_usage", new_callable=AsyncMock):
            mock_client = MockClient.return_value
            mock_client.messages.create = AsyncMock(return_value=response)
            events = await _collect_events(stream_agent_response(
                messages=[{"role": "user", "content": "hi"}],
                model="m", system="sys", tools=[], db_session=db_session,
            ))
        # The text block is emitted once as thinking (non-final turn).
        # max_tokens handler must NOT re-emit it.
        text_events = [e for e in events if e["type"] == "text"]
        assert len(text_events) == 0, "max_tokens path must not emit additional text chunks"
        # A done event must still be present.
        types = [e["type"] for e in events]
        assert "done" in types
        # Exactly one done event.
        assert types.count("done") == 1

    @pytest.mark.asyncio
    async def test_max_tokens_stop_reason_single_done(self):
        """Verify only a single 'done' event is emitted for max_tokens."""
        db_session = AsyncMock()
        response = _make_api_response(
            content=[],
            stop_reason="max_tokens",
        )
        with patch("agent.chat_handler.AsyncAnthropic") as MockClient, \
             patch("agent.chat_handler.record_llm_usage", new_callable=AsyncMock):
            mock_client = MockClient.return_value
            mock_client.messages.create = AsyncMock(return_value=response)
            events = await _collect_events(stream_agent_response(
                messages=[{"role": "user", "content": "hi"}],
                model="m", system="sys", tools=[], db_session=db_session,
            ))
        done_events = [e for e in events if e["type"] == "done"]
        assert len(done_events) == 1

    # ── CRITICAL 5: db_session.rollback() called after tool execution error ──

    @pytest.mark.asyncio
    async def test_db_rollback_called_on_tool_error(self):
        """db_session.rollback() must be called when execute_tool raises."""
        db_session = AsyncMock()
        response_1 = _make_api_response(
            content=[_make_tool_use_block("t_err", "search_reports", {})],
            stop_reason="tool_use",
        )
        response_2 = _make_api_response(
            content=[_make_text_block("handled error.")],
            stop_reason="end_turn",
        )
        with patch("agent.chat_handler.AsyncAnthropic") as MockClient, \
             patch("agent.chat_handler.execute_tool", new=AsyncMock(
                 side_effect=RuntimeError("DB gone")
             )), \
             patch("agent.chat_handler.record_llm_usage", new_callable=AsyncMock):
            mock_client = MockClient.return_value
            mock_client.messages.create = AsyncMock(side_effect=[response_1, response_2])
            events = await _collect_events(stream_agent_response(
                messages=[{"role": "user", "content": "search"}],
                model="m", system="sys", tools=[], db_session=db_session,
            ))
        # rollback must have been awaited exactly once
        db_session.rollback.assert_awaited_once()
        # the loop should still complete with a done event
        types = [e["type"] for e in events]
        assert "done" in types
