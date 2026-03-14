"""LLM 파서 테스트 — S2a(분류)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from parser.base import ParsedReport
from parser.llm_parser import classify_message, S2aResult


# --- fixtures ---

def _make_parsed(**overrides) -> ParsedReport:
    defaults = {
        "title": "삼성전자 목표주가 상향",
        "source_channel": "@repostory123",
        "raw_text": "미래에셋증권\n삼성전자(005930)\n목표주가 85,000원\n매수",
        "broker": "미래에셋",
        "stock_name": "삼성전자",
        "stock_code": "005930",
        "opinion": "매수",
        "target_price": 85000,
    }
    defaults.update(overrides)
    return ParsedReport(**defaults)


def _mock_response(tool_input: dict | None):
    response = MagicMock()
    response.usage = MagicMock(input_tokens=100, output_tokens=50)
    return tool_input, response


# ──────────────────────────────────────────────────────────────
# S2a: classify_message
# ──────────────────────────────────────────────────────────────

class TestClassifyMessage:

    @pytest.mark.asyncio
    async def test_broker_report(self):
        """broker_report 분류 → S2aResult.message_type == broker_report."""
        parsed = _make_parsed()
        with patch("parser.llm_parser._call_s2a", new_callable=AsyncMock,
                   return_value=_mock_response({"message_type": "broker_report"})), \
             patch("parser.llm_parser.record_llm_usage", new_callable=AsyncMock), \
             patch("parser.llm_parser.settings") as s:
            s.llm_enabled = True
            s.anthropic_api_key = "test"
            s.llm_model = "claude-haiku-4-5-20251001"
            result = await classify_message(parsed)
        assert result.message_type == "broker_report"
        assert result.reason is None

    @pytest.mark.asyncio
    async def test_news(self):
        parsed = _make_parsed()
        with patch("parser.llm_parser._call_s2a", new_callable=AsyncMock,
                   return_value=_mock_response({"message_type": "news"})), \
             patch("parser.llm_parser.record_llm_usage", new_callable=AsyncMock), \
             patch("parser.llm_parser.settings") as s:
            s.llm_enabled = True
            s.anthropic_api_key = "test"
            s.llm_model = "claude-haiku-4-5-20251001"
            result = await classify_message(parsed)
        assert result.message_type == "news"

    @pytest.mark.asyncio
    async def test_ambiguous_with_reason(self):
        """ambiguous 분류 → reason 포함."""
        parsed = _make_parsed()
        with patch("parser.llm_parser._call_s2a", new_callable=AsyncMock,
                   return_value=_mock_response({
                       "message_type": "ambiguous",
                       "reason": "텍스트가 잘려서 증권사를 확인할 수 없음",
                   })), \
             patch("parser.llm_parser.record_llm_usage", new_callable=AsyncMock), \
             patch("parser.llm_parser.settings") as s:
            s.llm_enabled = True
            s.anthropic_api_key = "test"
            s.llm_model = "claude-haiku-4-5-20251001"
            result = await classify_message(parsed)
        assert result.message_type == "ambiguous"
        assert "증권사" in result.reason

    @pytest.mark.asyncio
    async def test_llm_failure_passthrough(self):
        """S2a LLM 실패 → broker_report로 통과 (누락 방지)."""
        parsed = _make_parsed()
        with patch("parser.llm_parser._call_s2a", new_callable=AsyncMock,
                   side_effect=Exception("API error")), \
             patch("parser.llm_parser.settings") as s:
            s.llm_enabled = True
            s.anthropic_api_key = "test"
            result = await classify_message(parsed)
        assert result.message_type == "broker_report"

    @pytest.mark.asyncio
    async def test_disabled_passthrough(self):
        """LLM 비활성 → broker_report 통과."""
        parsed = _make_parsed()
        with patch("parser.llm_parser.settings") as s:
            s.llm_enabled = False
            result = await classify_message(parsed)
        assert result.message_type == "broker_report"

    @pytest.mark.asyncio
    async def test_usage_recorded_with_s2a_purpose(self):
        """S2a 호출 후 purpose='s2a_classify'로 usage 기록."""
        parsed = _make_parsed()
        with patch("parser.llm_parser._call_s2a", new_callable=AsyncMock,
                   return_value=_mock_response({"message_type": "broker_report"})), \
             patch("parser.llm_parser.record_llm_usage", new_callable=AsyncMock) as mock_record, \
             patch("parser.llm_parser.settings") as s:
            s.llm_enabled = True
            s.anthropic_api_key = "test"
            s.llm_model = "claude-haiku-4-5-20251001"
            await classify_message(parsed)
        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["purpose"] == "s2a_classify"


# ──────────────────────────────────────────────────────────────
# S2aResult
# ──────────────────────────────────────────────────────────────

class TestS2aResult:
    def test_basic(self):
        r = S2aResult("broker_report")
        assert r.message_type == "broker_report"
        assert r.reason is None

    def test_with_reason(self):
        r = S2aResult("ambiguous", "판단 불가")
        assert r.reason == "판단 불가"
