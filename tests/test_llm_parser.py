"""LLM 파서 테스트 — anthropic API를 mock하여 테스트."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from parser.base import ParsedReport
from parser.llm_parser import enrich_with_llm, _merge_field


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


def _mock_call_result(tool_input: dict | None):
    """_call_llm 반환값 mock — (tool_input, response) 튜플."""
    response = MagicMock()
    response.usage = MagicMock(input_tokens=100, output_tokens=50)
    return tool_input, response


# --- tests ---

class TestEnrichWithLLM:
    """enrich_with_llm 통합 테스트."""

    @pytest.mark.asyncio
    async def test_broker_report_overwrites_regex(self):
        """broker_report → LLM 값이 정규식 결과를 덮어쓰는지 확인."""
        parsed = _make_parsed(broker="미래에셋", stock_name="삼성전자잘못됨")

        llm_result = {
            "message_type": "broker_report",
            "broker": "미래에셋증권",
            "stock_name": "삼성전자",
            "title": "삼성전자 목표주가 상향 조정",
            "analyst": "김분석",
            "opinion": "Buy",
            "target_price": "90,000원",
        }

        with patch("parser.llm_parser._call_llm", new_callable=AsyncMock, return_value=_mock_call_result(llm_result)), \
             patch("parser.llm_parser.record_llm_usage", new_callable=AsyncMock), \
             patch("parser.llm_parser.settings") as mock_settings:
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.llm_model = "claude-haiku-4-5-20251001"

            result = await enrich_with_llm(parsed)

        assert result is not None
        assert result.broker == "미래에셋증권"  # LLM이 정규화
        assert result.stock_name == "삼성전자"  # LLM이 교정
        assert result.title == "삼성전자 목표주가 상향 조정"
        assert result.analyst == "김분석"
        assert result.opinion == "매수"  # normalize_opinion 적용
        assert result.target_price == 90000  # parse_price 적용

    @pytest.mark.asyncio
    async def test_news_returns_none(self):
        """news 분류 → None 반환."""
        parsed = _make_parsed()

        with patch("parser.llm_parser._call_llm", new_callable=AsyncMock, return_value=_mock_call_result({"message_type": "news"})), \
             patch("parser.llm_parser.record_llm_usage", new_callable=AsyncMock), \
             patch("parser.llm_parser.settings") as mock_settings:
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.llm_model = "claude-haiku-4-5-20251001"

            result = await enrich_with_llm(parsed)

        assert result is None

    @pytest.mark.asyncio
    async def test_general_returns_none(self):
        """general 분류 → None 반환."""
        parsed = _make_parsed()

        with patch("parser.llm_parser._call_llm", new_callable=AsyncMock, return_value=_mock_call_result({"message_type": "general"})), \
             patch("parser.llm_parser.record_llm_usage", new_callable=AsyncMock), \
             patch("parser.llm_parser.settings") as mock_settings:
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.llm_model = "claude-haiku-4-5-20251001"

            result = await enrich_with_llm(parsed)

        assert result is None

    @pytest.mark.asyncio
    async def test_llm_failure_fallback(self):
        """LLM 실패 시 → 기존 ParsedReport 반환 (fallback)."""
        parsed = _make_parsed()

        with patch("parser.llm_parser._call_llm", new_callable=AsyncMock, side_effect=Exception("API error")), \
             patch("parser.llm_parser.settings") as mock_settings:
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"

            result = await enrich_with_llm(parsed)

        assert result is not None
        assert result.broker == "미래에셋"  # 원래 정규식 값 유지
        assert result.title == "삼성전자 목표주가 상향"

    @pytest.mark.asyncio
    async def test_llm_disabled_passthrough(self):
        """LLM 비활성 시 → passthrough."""
        parsed = _make_parsed()

        with patch("parser.llm_parser.settings") as mock_settings:
            mock_settings.llm_enabled = False

            result = await enrich_with_llm(parsed)

        assert result is parsed  # 동일 객체

    @pytest.mark.asyncio
    async def test_no_api_key_passthrough(self):
        """API 키 없을 때 → passthrough."""
        parsed = _make_parsed()

        with patch("parser.llm_parser.settings") as mock_settings:
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = None

            result = await enrich_with_llm(parsed)

        assert result is parsed

    @pytest.mark.asyncio
    async def test_llm_null_result_fallback(self):
        """LLM이 tool_use 결과를 반환하지 않은 경우 → fallback."""
        parsed = _make_parsed()

        with patch("parser.llm_parser._call_llm", new_callable=AsyncMock, return_value=_mock_call_result(None)), \
             patch("parser.llm_parser.record_llm_usage", new_callable=AsyncMock), \
             patch("parser.llm_parser.settings") as mock_settings:
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.llm_model = "claude-haiku-4-5-20251001"

            result = await enrich_with_llm(parsed)

        assert result is not None
        assert result.broker == "미래에셋"

    @pytest.mark.asyncio
    async def test_usage_is_recorded(self):
        """broker_report 분류 후 _record_usage가 호출됐는지 확인."""
        parsed = _make_parsed()
        llm_result = {"message_type": "broker_report", "broker": "KB증권"}

        with patch("parser.llm_parser._call_llm", new_callable=AsyncMock, return_value=_mock_call_result(llm_result)) as mock_call, \
             patch("parser.llm_parser.record_llm_usage", new_callable=AsyncMock) as mock_record, \
             patch("parser.llm_parser.settings") as mock_settings:
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.llm_model = "claude-haiku-4-5-20251001"

            await enrich_with_llm(parsed)

        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["purpose"] == "parse"


class TestMergeField:
    """_merge_field 단위 테스트."""

    def test_llm_value_wins(self):
        assert _merge_field("정규식값", "LLM값") == "LLM값"

    def test_regex_kept_when_llm_empty(self):
        assert _merge_field("정규식값", None) == "정규식값"
        assert _merge_field("정규식값", "") == "정규식값"

    def test_normalizer_applied(self):
        from parser.normalizer import normalize_broker
        result = _merge_field("미래에셋", "미래에셋대우", normalize_broker)
        assert result == "미래에셋증권"

    def test_normalizer_not_applied_when_no_llm_value(self):
        from parser.normalizer import normalize_broker
        result = _merge_field("미래에셋", None, normalize_broker)
        assert result == "미래에셋"

    def test_opinion_normalizer(self):
        from parser.normalizer import normalize_opinion
        assert _merge_field("매수", "BUY", normalize_opinion) == "매수"
        assert _merge_field("매수", "HOLD", normalize_opinion) == "중립"
        assert _merge_field(None, "Sell", normalize_opinion) == "매도"


class TestEnrichMergeLogic:
    """LLM 보강 시 merge 로직 상세 테스트."""

    @pytest.mark.asyncio
    async def test_partial_llm_result(self):
        """LLM이 일부 필드만 반환 → 나머지는 정규식 값 유지."""
        parsed = _make_parsed(broker="KB", analyst="기존분석가", opinion="매수", target_price=80000)
        llm_result = {"message_type": "broker_report", "broker": "KB증권"}

        with patch("parser.llm_parser._call_llm", new_callable=AsyncMock, return_value=_mock_call_result(llm_result)), \
             patch("parser.llm_parser.record_llm_usage", new_callable=AsyncMock), \
             patch("parser.llm_parser.settings") as mock_settings:
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.llm_model = "claude-haiku-4-5-20251001"

            result = await enrich_with_llm(parsed)

        assert result.broker == "KB증권"
        assert result.analyst == "기존분석가"
        assert result.opinion == "매수"
        assert result.target_price == 80000

    @pytest.mark.asyncio
    async def test_llm_corrects_regex_misparsing(self):
        """정규식이 잘못 파싱한 broker를 LLM이 교정."""
        parsed = _make_parsed(
            broker="[**미래에셋증권**](http://link)",
            stock_name="삼성전자(**005930**)",
        )
        llm_result = {
            "message_type": "broker_report",
            "broker": "미래에셋",
            "stock_name": "삼성전자",
            "stock_code": "005930",
        }

        with patch("parser.llm_parser._call_llm", new_callable=AsyncMock, return_value=_mock_call_result(llm_result)), \
             patch("parser.llm_parser.record_llm_usage", new_callable=AsyncMock), \
             patch("parser.llm_parser.settings") as mock_settings:
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.llm_model = "claude-haiku-4-5-20251001"

            result = await enrich_with_llm(parsed)

        assert result.broker == "미래에셋증권"
        assert result.stock_name == "삼성전자"
        assert result.stock_code == "005930"

    @pytest.mark.asyncio
    async def test_prev_fields_merge(self):
        """이전 목표주가/의견 필드도 LLM 값으로 덮어씀."""
        parsed = _make_parsed(prev_opinion=None, prev_target_price=None)
        llm_result = {
            "message_type": "broker_report",
            "prev_opinion": "Hold",
            "prev_target_price": "75,000원",
        }

        with patch("parser.llm_parser._call_llm", new_callable=AsyncMock, return_value=_mock_call_result(llm_result)), \
             patch("parser.llm_parser.record_llm_usage", new_callable=AsyncMock), \
             patch("parser.llm_parser.settings") as mock_settings:
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.llm_model = "claude-haiku-4-5-20251001"

            result = await enrich_with_llm(parsed)

        assert result.prev_opinion == "중립"
        assert result.prev_target_price == 75000
