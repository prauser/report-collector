"""LLM 파서 테스트 — anthropic API를 mock하여 테스트."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from dataclasses import dataclass

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


def _mock_tool_block(input_data: dict):
    """tool_use 블록 mock."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = "classify_and_extract"
    block.input = input_data
    return block


def _mock_response(tool_input: dict):
    """API 응답 mock."""
    response = MagicMock()
    response.content = [_mock_tool_block(tool_input)]
    return response


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

        with patch("parser.llm_parser._call_llm", new_callable=AsyncMock, return_value=llm_result), \
             patch("parser.llm_parser.settings") as mock_settings:
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"

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
        llm_result = {"message_type": "news"}

        with patch("parser.llm_parser._call_llm", new_callable=AsyncMock, return_value=llm_result), \
             patch("parser.llm_parser.settings") as mock_settings:
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"

            result = await enrich_with_llm(parsed)

        assert result is None

    @pytest.mark.asyncio
    async def test_general_returns_none(self):
        """general 분류 → None 반환."""
        parsed = _make_parsed()
        llm_result = {"message_type": "general"}

        with patch("parser.llm_parser._call_llm", new_callable=AsyncMock, return_value=llm_result), \
             patch("parser.llm_parser.settings") as mock_settings:
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"

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

        with patch("parser.llm_parser._call_llm", new_callable=AsyncMock, return_value=None), \
             patch("parser.llm_parser.settings") as mock_settings:
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"

            result = await enrich_with_llm(parsed)

        assert result is not None
        assert result.broker == "미래에셋"


class TestMergeField:
    """_merge_field 단위 테스트."""

    def test_llm_value_wins(self):
        """LLM 값이 있으면 덮어씀."""
        assert _merge_field("정규식값", "LLM값") == "LLM값"

    def test_regex_kept_when_llm_empty(self):
        """LLM 값이 없으면 정규식 값 유지."""
        assert _merge_field("정규식값", None) == "정규식값"
        assert _merge_field("정규식값", "") == "정규식값"

    def test_normalizer_applied(self):
        """normalizer가 LLM 값에 적용됨."""
        from parser.normalizer import normalize_broker
        result = _merge_field("미래에셋", "미래에셋대우", normalize_broker)
        assert result == "미래에셋증권"  # 정규화됨

    def test_normalizer_not_applied_when_no_llm_value(self):
        """LLM 값이 없으면 normalizer 적용 안 됨."""
        from parser.normalizer import normalize_broker
        result = _merge_field("미래에셋", None, normalize_broker)
        assert result == "미래에셋"  # 정규식 값 그대로

    def test_opinion_normalizer(self):
        """투자의견 normalizer 연동."""
        from parser.normalizer import normalize_opinion
        assert _merge_field("매수", "BUY", normalize_opinion) == "매수"
        assert _merge_field("매수", "HOLD", normalize_opinion) == "중립"
        assert _merge_field(None, "Sell", normalize_opinion) == "매도"


class TestEnrichMergeLogic:
    """LLM 보강 시 merge 로직 상세 테스트."""

    @pytest.mark.asyncio
    async def test_partial_llm_result(self):
        """LLM이 일부 필드만 반환 → 나머지는 정규식 값 유지."""
        parsed = _make_parsed(
            broker="KB",
            analyst="기존분석가",
            opinion="매수",
            target_price=80000,
        )

        llm_result = {
            "message_type": "broker_report",
            "broker": "KB증권",
            # stock_name, analyst, opinion 등은 반환 안 함
        }

        with patch("parser.llm_parser._call_llm", new_callable=AsyncMock, return_value=llm_result), \
             patch("parser.llm_parser.settings") as mock_settings:
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"

            result = await enrich_with_llm(parsed)

        assert result.broker == "KB증권"  # LLM 값
        assert result.analyst == "기존분석가"  # 정규식 값 유지
        assert result.opinion == "매수"  # 정규식 값 유지
        assert result.target_price == 80000  # 정규식 값 유지

    @pytest.mark.asyncio
    async def test_llm_corrects_regex_misparsing(self):
        """정규식이 잘못 파싱한 broker를 LLM이 교정."""
        parsed = _make_parsed(
            broker="[**미래에셋증권**](http://link)",  # 정규식 오파싱
            stock_name="삼성전자(**005930**)",  # 마크다운 쓰레기
        )

        llm_result = {
            "message_type": "broker_report",
            "broker": "미래에셋",
            "stock_name": "삼성전자",
            "stock_code": "005930",
        }

        with patch("parser.llm_parser._call_llm", new_callable=AsyncMock, return_value=llm_result), \
             patch("parser.llm_parser.settings") as mock_settings:
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"

            result = await enrich_with_llm(parsed)

        assert result.broker == "미래에셋증권"  # normalize_broker 적용
        assert result.stock_name == "삼성전자"  # 깨끗하게 교정
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

        with patch("parser.llm_parser._call_llm", new_callable=AsyncMock, return_value=llm_result), \
             patch("parser.llm_parser.settings") as mock_settings:
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"

            result = await enrich_with_llm(parsed)

        assert result.prev_opinion == "중립"  # normalize_opinion
        assert result.prev_target_price == 75000  # parse_price
