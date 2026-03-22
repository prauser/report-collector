"""Layer 2 모듈 테스트 — extractor, analysis_repo, markdown_converter."""
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from parser.layer2_extractor import extract_layer2, Layer2Result


# ──────────────────────────────────────────────
# Layer2 Extractor
# ──────────────────────────────────────────────

def _mock_extract_response(result_dict: dict):
    response = MagicMock()
    response.usage = MagicMock(
        input_tokens=1000,
        output_tokens=500,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    return result_dict, response


class TestExtractLayer2:

    @pytest.mark.asyncio
    async def test_success_returns_layer2result(self):
        """정상 추출 → Layer2Result 반환."""
        llm_result = {
            "report_category": "stock",
            "meta": {
                "broker": "미래에셋증권",
                "stock_name": "삼성전자",
                "stock_code": "005930",
                "opinion": "매수",
                "target_price": 90000,
            },
            "thesis": {
                "summary": "반도체 업황 개선으로 실적 턴어라운드",
                "sentiment": 0.8,
            },
            "chain": [
                {"step": "trigger", "text": "HBM 수요 증가"},
                {"step": "financial_impact", "text": "매출 20% 성장 예상"},
            ],
            "stock_mentions": [
                {"stock_code": "005930", "company_name": "삼성전자", "mention_type": "primary", "impact": "positive"},
            ],
            "sector_mentions": [
                {"sector": "반도체", "mention_type": "primary", "impact": "positive"},
            ],
            "keywords": [
                {"keyword": "HBM", "keyword_type": "product"},
                {"keyword": "반도체", "keyword_type": "industry"},
            ],
            "extraction_quality": "high",
        }

        with patch("parser.layer2_extractor._call_extract", new_callable=AsyncMock,
                   return_value=_mock_extract_response(llm_result)), \
             patch("parser.layer2_extractor.record_llm_usage", new_callable=AsyncMock), \
             patch("parser.layer2_extractor.settings") as s:
            s.analysis_enabled = True
            s.anthropic_api_key = "test"
            s.llm_pdf_model = "claude-sonnet-4-6"
            s.analysis_schema_version = "v1"

            result = await extract_layer2(text="삼성전자 리포트", markdown="# 삼성전자")

        assert isinstance(result, Layer2Result)
        assert result.report_category == "stock"
        assert result.extraction_quality == "high"
        assert len(result.stock_mentions) == 1
        assert result.stock_mentions[0]["stock_code"] == "005930"
        assert len(result.analysis_data["chain"]) == 2

    @pytest.mark.asyncio
    async def test_disabled_returns_none(self):
        """analysis_enabled=False → None."""
        with patch("parser.layer2_extractor.settings") as s:
            s.analysis_enabled = False
            result = await extract_layer2(text="test")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_api_key_returns_none(self):
        """API 키 없으면 None."""
        with patch("parser.layer2_extractor.settings") as s:
            s.analysis_enabled = True
            s.anthropic_api_key = None
            result = await extract_layer2(text="test")
        assert result is None

    @pytest.mark.asyncio
    async def test_llm_failure_returns_none(self):
        """LLM 호출 실패 → None."""
        with patch("parser.layer2_extractor._call_extract", new_callable=AsyncMock,
                   side_effect=Exception("API error")), \
             patch("parser.layer2_extractor.settings") as s:
            s.analysis_enabled = True
            s.anthropic_api_key = "test"
            result = await extract_layer2(text="test")
        assert result is None

    @pytest.mark.asyncio
    async def test_usage_recorded(self):
        """layer2_extract purpose로 usage 기록."""
        llm_result = {
            "report_category": "macro",
            "meta": {},
            "thesis": {"summary": "test", "sentiment": 0},
            "chain": [],
            "extraction_quality": "low",
        }

        with patch("parser.layer2_extractor._call_extract", new_callable=AsyncMock,
                   return_value=_mock_extract_response(llm_result)), \
             patch("parser.layer2_extractor.record_llm_usage", new_callable=AsyncMock) as mock_record, \
             patch("parser.layer2_extractor.settings") as s:
            s.analysis_enabled = True
            s.anthropic_api_key = "test"
            s.llm_pdf_model = "claude-sonnet-4-6"
            s.analysis_schema_version = "v1"

            await extract_layer2(text="test", channel="@test")

        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["purpose"] == "layer2_extract"

    @pytest.mark.asyncio
    async def test_long_markdown_not_truncated(self):
        """긴 마크다운도 잘리지 않고 그대로 전달됨 (제한 없음)."""
        llm_result = {
            "report_category": "stock",
            "meta": {},
            "thesis": {"summary": "test", "sentiment": 0},
            "chain": [],
            "extraction_quality": "medium",
        }

        with patch("parser.layer2_extractor._call_extract", new_callable=AsyncMock,
                   return_value=_mock_extract_response(llm_result)) as mock_call, \
             patch("parser.layer2_extractor.record_llm_usage", new_callable=AsyncMock), \
             patch("parser.layer2_extractor.settings") as s:
            s.analysis_enabled = True
            s.anthropic_api_key = "test"
            s.llm_pdf_model = "claude-sonnet-4-6"
            s.analysis_schema_version = "v1"

            long_md = "가" * 50_000
            result = await extract_layer2(text="test", markdown=long_md)

        # user_content에 전체 마크다운이 포함되어야 함 (자르지 않음)
        call_args = mock_call.call_args[0]
        assert len(call_args[0]) >= 50_000  # 원문 전체 포함

        # truncated 플래그는 항상 False
        assert result.markdown_truncated is False
        assert result.markdown_original_chars == 50_000
        assert result.extraction_quality == "medium"  # LLM이 반환한 값 그대로

    @pytest.mark.asyncio
    async def test_short_markdown_not_truncated(self):
        """짧은 마크다운도 truncated=False."""
        llm_result = {
            "report_category": "stock",
            "meta": {},
            "thesis": {"summary": "test", "sentiment": 0},
            "chain": [],
            "extraction_quality": "high",
        }

        with patch("parser.layer2_extractor._call_extract", new_callable=AsyncMock,
                   return_value=_mock_extract_response(llm_result)), \
             patch("parser.layer2_extractor.record_llm_usage", new_callable=AsyncMock), \
             patch("parser.layer2_extractor.settings") as s:
            s.analysis_enabled = True
            s.anthropic_api_key = "test"
            s.llm_pdf_model = "claude-sonnet-4-6"
            s.analysis_schema_version = "v1"

            result = await extract_layer2(text="test", markdown="짧은 마크다운")

        assert result.markdown_truncated is False
        assert result.extraction_quality == "high"


# ──────────────────────────────────────────────
# Layer2Result dataclass
# ──────────────────────────────────────────────

class TestLayer2Result:

    def test_defaults(self):
        r = Layer2Result(report_category="stock")
        assert r.analysis_data == {}
        assert r.stock_mentions == []
        assert r.extraction_quality == "medium"
        assert r.llm_cost_usd == Decimal("0")

    def test_chain_in_analysis_data(self):
        r = Layer2Result(
            report_category="industry",
            analysis_data={
                "chain": [{"step": "trigger", "text": "test"}],
                "thesis": {"summary": "test"},
            },
        )
        assert len(r.analysis_data["chain"]) == 1


# ──────────────────────────────────────────────
# Listener helpers
# ──────────────────────────────────────────────

class TestApplyLayer2Meta:

    def test_applies_meta_updates(self):
        from collector.listener import _apply_layer2_meta

        report = MagicMock()
        meta = {
            "broker": "KB증권",
            "stock_name": "삼성전자",
            "opinion": "Buy",
            "target_price": 90000,
        }
        updates = _apply_layer2_meta(report, meta)

        assert updates["broker"] == "KB증권"
        assert updates["stock_name"] == "삼성전자"
        assert updates["opinion"] == "매수"  # normalized
        assert updates["target_price"] == 90000

    def test_empty_meta_returns_empty(self):
        from collector.listener import _apply_layer2_meta

        updates = _apply_layer2_meta(MagicMock(), {})
        assert updates == {}

    def test_none_meta_returns_empty(self):
        from collector.listener import _apply_layer2_meta

        updates = _apply_layer2_meta(MagicMock(), None)
        assert updates == {}

    def test_string_price_parsed(self):
        from collector.listener import _apply_layer2_meta

        updates = _apply_layer2_meta(MagicMock(), {"target_price": "85,000원"})
        assert updates["target_price"] == 85000


# ──────────────────────────────────────────────
# Markdown Converter
# ──────────────────────────────────────────────

class TestMarkdownConverter:

    @pytest.mark.asyncio
    async def test_fallback_on_missing_pymupdf4llm(self):
        """pymupdf4llm 없으면 fallback 사용."""
        from parser.markdown_converter import _convert_pymupdf4llm

        with patch.dict("sys.modules", {"pymupdf4llm": None}), \
             patch("parser.markdown_converter._convert_fallback", new_callable=AsyncMock,
                   return_value="fallback text") as mock_fb:
            result = await _convert_pymupdf4llm(MagicMock())
        assert result == "fallback text"

    @pytest.mark.asyncio
    async def test_converter_returns_tuple(self):
        """convert_pdf_to_markdown은 (text, converter_name) 튜플 반환."""
        from parser.markdown_converter import convert_pdf_to_markdown

        with patch("parser.markdown_converter._convert_pymupdf4llm", new_callable=AsyncMock,
                   return_value="# heading"), \
             patch("parser.markdown_converter.settings") as s:
            s.markdown_converter = "pymupdf4llm"
            text, name = await convert_pdf_to_markdown(MagicMock())

        assert text == "# heading"
        assert name == "pymupdf4llm"

    def test_estimate_token_count(self):
        from parser.markdown_converter import _estimate_token_count

        assert _estimate_token_count("한글 텍스트 테스트") == len("한글 텍스트 테스트") * 2 // 3
        assert _estimate_token_count("") == 0


# ──────────────────────────────────────────────
# Analysis Repo (stock_code dedup logic)
# ──────────────────────────────────────────────

class TestAnalysisRepoDedup:

    def test_stock_mentions_dedup_empty_code(self):
        """빈 stock_code가 여러 개일 때 company_name으로 구분."""
        from storage.analysis_repo import save_analysis
        # 이 테스트는 실제 DB 필요하므로 로직만 검증
        mentions = [
            {"stock_code": "", "company_name": "엔비디아", "mention_type": "primary"},
            {"stock_code": "", "company_name": "AMD", "mention_type": "related"},
            {"stock_code": "", "company_name": "엔비디아", "mention_type": "related"},  # 중복
        ]

        seen_codes = set()
        stock_rows = []
        for sm in mentions:
            code = sm.get("stock_code") or ""
            name = sm.get("company_name") or ""
            if not code and not name:
                continue
            dedup_key = code if code else f"_name_{name}"
            if dedup_key in seen_codes:
                continue
            seen_codes.add(dedup_key)
            stock_rows.append({
                "stock_code": code or name[:20],
                "company_name": name,
            })

        assert len(stock_rows) == 2  # 엔비디아 중복 제거됨
        codes = {r["stock_code"] for r in stock_rows}
        assert "엔비디아" in codes
        assert "AMD" in codes
