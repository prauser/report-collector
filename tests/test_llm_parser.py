"""LLM 파서 테스트 — S2a(분류) + S2b(추출) 분리 구조."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from parser.base import ParsedReport
from parser.llm_parser import classify_message, extract_metadata, S2aResult


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
# S2b: extract_metadata
# ──────────────────────────────────────────────────────────────

class TestExtractMetadata:

    @pytest.mark.asyncio
    async def test_overwrites_regex_values(self):
        """LLM 추출 값이 정규식 결과를 덮어씀."""
        parsed = _make_parsed(broker="미래에셋", stock_name="삼성전자잘못됨")
        llm_result = {
            "broker": "미래에셋증권",
            "stock_name": "삼성전자",
            "title": "삼성전자 목표주가 상향 조정",
            "analyst": "김분석",
            "opinion": "Buy",
            "target_price": "90,000원",
        }
        with patch("parser.llm_parser._call_s2b", new_callable=AsyncMock,
                   return_value=_mock_response(llm_result)), \
             patch("parser.llm_parser.record_llm_usage", new_callable=AsyncMock), \
             patch("parser.llm_parser.settings") as s:
            s.llm_enabled = True
            s.anthropic_api_key = "test"
            s.llm_model = "claude-haiku-4-5-20251001"
            result = await extract_metadata(parsed)

        assert result.broker == "미래에셋증권"
        assert result.stock_name == "삼성전자"
        assert result.title == "삼성전자 목표주가 상향 조정"
        assert result.analyst == "김분석"
        assert result.opinion == "매수"      # normalize_opinion 적용
        assert result.target_price == 90000  # parse_price 적용

    @pytest.mark.asyncio
    async def test_partial_result_keeps_regex(self):
        """LLM이 일부 필드만 반환 → 나머지는 정규식 값 유지."""
        parsed = _make_parsed(broker="KB", analyst="기존분석가", opinion="매수", target_price=80000)
        with patch("parser.llm_parser._call_s2b", new_callable=AsyncMock,
                   return_value=_mock_response({"broker": "KB증권"})), \
             patch("parser.llm_parser.record_llm_usage", new_callable=AsyncMock), \
             patch("parser.llm_parser.settings") as s:
            s.llm_enabled = True
            s.anthropic_api_key = "test"
            s.llm_model = "claude-haiku-4-5-20251001"
            result = await extract_metadata(parsed)

        assert result.broker == "KB증권"
        assert result.analyst == "기존분석가"  # 유지
        assert result.opinion == "매수"        # 유지
        assert result.target_price == 80000    # 유지

    @pytest.mark.asyncio
    async def test_prev_fields_extracted(self):
        """이전 목표주가/의견 추출."""
        parsed = _make_parsed(prev_opinion=None, prev_target_price=None)
        with patch("parser.llm_parser._call_s2b", new_callable=AsyncMock,
                   return_value=_mock_response({
                       "prev_opinion": "Hold",
                       "prev_target_price": "75,000원",
                   })), \
             patch("parser.llm_parser.record_llm_usage", new_callable=AsyncMock), \
             patch("parser.llm_parser.settings") as s:
            s.llm_enabled = True
            s.anthropic_api_key = "test"
            s.llm_model = "claude-haiku-4-5-20251001"
            result = await extract_metadata(parsed)

        assert result.prev_opinion == "중립"
        assert result.prev_target_price == 75000

    @pytest.mark.asyncio
    async def test_llm_failure_fallback(self):
        """S2b LLM 실패 → 기존 ParsedReport 반환."""
        parsed = _make_parsed()
        with patch("parser.llm_parser._call_s2b", new_callable=AsyncMock,
                   side_effect=Exception("API error")), \
             patch("parser.llm_parser.settings") as s:
            s.llm_enabled = True
            s.anthropic_api_key = "test"
            result = await extract_metadata(parsed)

        assert result.broker == "미래에셋"  # 원래 정규식 값 유지
        assert result.title == "삼성전자 목표주가 상향"

    @pytest.mark.asyncio
    async def test_pdf_meta_context_passed(self):
        """pdf_meta_context가 있으면 _call_s2b에 전달됨."""
        parsed = _make_parsed()
        with patch("parser.llm_parser._call_s2b", new_callable=AsyncMock,
                   return_value=_mock_response({"broker": "하나증권"})) as mock_call, \
             patch("parser.llm_parser.record_llm_usage", new_callable=AsyncMock), \
             patch("parser.llm_parser.settings") as s:
            s.llm_enabled = True
            s.anthropic_api_key = "test"
            s.llm_model = "claude-haiku-4-5-20251001"
            await extract_metadata(parsed, pdf_meta_context="Author: 하나증권\nKeywords: (005930)")

        _, kwargs = mock_call.call_args
        # 두 번째 인자(pdf_meta_context)가 전달됐는지 확인
        call_args = mock_call.call_args[0]
        assert "하나증권" in call_args[1]

    @pytest.mark.asyncio
    async def test_usage_recorded_with_s2b_purpose(self):
        """S2b 호출 후 purpose='s2b_extract'로 usage 기록."""
        parsed = _make_parsed()
        with patch("parser.llm_parser._call_s2b", new_callable=AsyncMock,
                   return_value=_mock_response({"broker": "KB증권"})), \
             patch("parser.llm_parser.record_llm_usage", new_callable=AsyncMock) as mock_record, \
             patch("parser.llm_parser.settings") as s:
            s.llm_enabled = True
            s.anthropic_api_key = "test"
            s.llm_model = "claude-haiku-4-5-20251001"
            await extract_metadata(parsed)

        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["purpose"] == "s2b_extract"

    @pytest.mark.asyncio
    async def test_disabled_passthrough(self):
        """LLM 비활성 → 원본 반환."""
        parsed = _make_parsed()
        with patch("parser.llm_parser.settings") as s:
            s.llm_enabled = False
            result = await extract_metadata(parsed)
        assert result is parsed


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
