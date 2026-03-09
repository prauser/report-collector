"""PDF 분석기 테스트 — LLM API와 파일 시스템을 mock."""
import pytest
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch, mock_open

from db.models import Report
from parser.pdf_analyzer import analyze_pdf, extract_pdf_text


# --- helpers ---

def _make_report(**overrides) -> Report:
    report = MagicMock(spec=Report)
    report.id = 1
    report.broker = "미래에셋증권"
    report.stock_name = "삼성전자"
    report.title = "삼성전자 목표주가 상향"
    report.opinion = "매수"
    report.target_price = 85000
    report.source_channel = "@repostory123"
    report.pdf_path = "2024/01/20240101_test.pdf"
    for k, v in overrides.items():
        setattr(report, k, v)
    return report


def _mock_llm_result(summary="요약", sentiment=0.7, keywords=None):
    result = {
        "summary": summary,
        "sentiment": sentiment,
        "keywords": keywords or ["반도체", "매수", "목표주가상향"],
    }
    response = MagicMock()
    response.usage = MagicMock(input_tokens=500, output_tokens=200)
    block = MagicMock()
    block.type = "tool_use"
    block.name = "analyze_report"
    block.input = result
    response.content = [block]
    return result, response


# --- tests ---

class TestAnalyzePdf:

    @pytest.mark.asyncio
    async def test_success_returns_analysis(self, tmp_path):
        """정상 케이스 — summary/sentiment/keywords 반환."""
        report = _make_report()
        pdf_file = tmp_path / "test.pdf"
        pdf_file.write_bytes(b"%PDF-1.4 test")

        llm_result, llm_response = _mock_llm_result(
            summary="삼성전자 목표주가 90,000원으로 상향. 반도체 업황 개선 기대.",
            sentiment=0.75,
            keywords=["반도체", "목표주가상향", "매수"],
        )

        with patch("parser.pdf_analyzer.settings") as mock_settings, \
             patch("parser.pdf_analyzer.extract_pdf_text", return_value="PDF 본문 텍스트"), \
             patch("parser.pdf_analyzer._call_llm", new_callable=AsyncMock, return_value=(llm_result, llm_response)), \
             patch("parser.pdf_analyzer.record_llm_usage", new_callable=AsyncMock), \
             patch("pathlib.Path.exists", return_value=True):
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.llm_pdf_model = "claude-sonnet-4-6"
            mock_settings.pdf_base_path = tmp_path

            result = await analyze_pdf(report)

        assert result is not None
        assert "목표주가" in result["summary"]
        assert result["sentiment"] == Decimal("0.75")
        assert "반도체" in result["keywords"]

    @pytest.mark.asyncio
    async def test_no_pdf_path_returns_none(self):
        """pdf_path 없으면 None."""
        report = _make_report(pdf_path=None)

        with patch("parser.pdf_analyzer.settings") as mock_settings:
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"

            result = await analyze_pdf(report)

        assert result is None

    @pytest.mark.asyncio
    async def test_pdf_file_not_found_returns_none(self, tmp_path):
        """PDF 파일이 없으면 None."""
        report = _make_report()

        with patch("parser.pdf_analyzer.settings") as mock_settings, \
             patch("pathlib.Path.exists", return_value=False):
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.pdf_base_path = tmp_path

            result = await analyze_pdf(report)

        assert result is None

    @pytest.mark.asyncio
    async def test_llm_disabled_returns_none(self):
        """LLM 비활성이면 None."""
        report = _make_report()

        with patch("parser.pdf_analyzer.settings") as mock_settings:
            mock_settings.llm_enabled = False

            result = await analyze_pdf(report)

        assert result is None

    @pytest.mark.asyncio
    async def test_llm_failure_returns_none(self, tmp_path):
        """LLM 호출 실패 시 None (fallback 없음 — caller가 처리)."""
        report = _make_report()

        with patch("parser.pdf_analyzer.settings") as mock_settings, \
             patch("parser.pdf_analyzer.extract_pdf_text", return_value="PDF 본문"), \
             patch("parser.pdf_analyzer._call_llm", new_callable=AsyncMock, side_effect=Exception("API error")), \
             patch("pathlib.Path.exists", return_value=True):
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.pdf_base_path = tmp_path

            result = await analyze_pdf(report)

        assert result is None

    @pytest.mark.asyncio
    async def test_sentiment_clipped_to_range(self, tmp_path):
        """sentiment 범위 초과 시 -1.0~1.0으로 클리핑."""
        report = _make_report()
        llm_result, llm_response = _mock_llm_result(sentiment=2.5)  # 범위 초과

        with patch("parser.pdf_analyzer.settings") as mock_settings, \
             patch("parser.pdf_analyzer.extract_pdf_text", return_value="본문"), \
             patch("parser.pdf_analyzer._call_llm", new_callable=AsyncMock, return_value=(llm_result, llm_response)), \
             patch("parser.pdf_analyzer.record_llm_usage", new_callable=AsyncMock), \
             patch("pathlib.Path.exists", return_value=True):
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.llm_pdf_model = "claude-sonnet-4-6"
            mock_settings.pdf_base_path = tmp_path

            result = await analyze_pdf(report)

        assert result["sentiment"] == Decimal("1.0")

    @pytest.mark.asyncio
    async def test_usage_recorded_with_pdf_analysis_purpose(self, tmp_path):
        """purpose='pdf_analysis'로 usage가 기록되는지 확인."""
        report = _make_report()
        llm_result, llm_response = _mock_llm_result()

        with patch("parser.pdf_analyzer.settings") as mock_settings, \
             patch("parser.pdf_analyzer.extract_pdf_text", return_value="본문"), \
             patch("parser.pdf_analyzer._call_llm", new_callable=AsyncMock, return_value=(llm_result, llm_response)), \
             patch("parser.pdf_analyzer.record_llm_usage", new_callable=AsyncMock) as mock_record, \
             patch("pathlib.Path.exists", return_value=True):
            mock_settings.llm_enabled = True
            mock_settings.anthropic_api_key = "test-key"
            mock_settings.llm_pdf_model = "claude-sonnet-4-6"
            mock_settings.pdf_base_path = tmp_path

            await analyze_pdf(report)

        mock_record.assert_called_once()
        assert mock_record.call_args.kwargs["purpose"] == "pdf_analysis"
        assert mock_record.call_args.kwargs["report_id"] == report.id
        assert mock_record.call_args.kwargs["model"] == "claude-sonnet-4-6"


class TestExtractPdfText:

    def test_empty_pdf_returns_none(self, tmp_path):
        """텍스트 없는 PDF → None."""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = ""
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        mock_pypdf = MagicMock()
        mock_pypdf.PdfReader.return_value = mock_reader

        with patch.dict("sys.modules", {"pypdf": mock_pypdf}):
            result = extract_pdf_text(tmp_path / "empty.pdf")

        assert result is None

    def test_text_truncated_to_max_chars(self, tmp_path):
        """긴 텍스트는 PDF_TEXT_MAX_CHARS로 잘림."""
        from parser.pdf_analyzer import PDF_TEXT_MAX_CHARS
        long_text = "가" * (PDF_TEXT_MAX_CHARS + 5000)

        mock_page = MagicMock()
        mock_page.extract_text.return_value = long_text
        mock_reader = MagicMock()
        mock_reader.pages = [mock_page]

        mock_pypdf = MagicMock()
        mock_pypdf.PdfReader.return_value = mock_reader

        with patch.dict("sys.modules", {"pypdf": mock_pypdf}):
            result = extract_pdf_text(tmp_path / "long.pdf")

        assert result is not None
        assert len(result) == PDF_TEXT_MAX_CHARS
