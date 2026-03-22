"""PDF 아카이빙 테스트."""
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import date


def make_report(**kwargs):
    r = MagicMock()
    r.id = 1
    r.broker = "테스트증권"
    r.report_date = date(2026, 3, 8)
    r.stock_name = "삼성전자"
    r.sector = None
    r.title = "반도체업황개선"
    r.title_normalized = "반도체업황개선"
    r.pdf_url = "https://example.com/report.pdf"
    r.pdf_path = None
    r.pdf_download_failed = False
    for k, v in kwargs.items():
        setattr(r, k, v)
    return r


class TestBuildPdfPath:

    def test_stock_report_path(self):
        from storage.pdf_archiver import build_pdf_path
        report = make_report()
        path = build_pdf_path(report)
        assert str(path).startswith("2026")
        assert "20260308" in str(path)
        assert "테스트증권" in str(path)
        assert str(path).endswith(".pdf")

    def test_industry_report_path(self):
        from storage.pdf_archiver import build_pdf_path
        report = make_report(stock_name=None, sector="반도체")
        path = build_pdf_path(report)
        assert "산업_" in str(path)

    def test_no_special_chars_in_filename(self):
        from storage.pdf_archiver import build_pdf_path
        report = make_report(title="리포트: 상반기 전망 / 하반기 대비")
        path = build_pdf_path(report)
        assert ":" not in path.name
        assert "/" not in path.name


class TestDownloadPdf:

    @pytest.mark.asyncio
    async def test_successful_download(self, tmp_path):
        from storage.pdf_archiver import download_pdf
        report = make_report()

        fake_content = b"%PDF-1.4 fake content"

        with patch("storage.pdf_archiver.settings") as mock_settings:
            mock_settings.pdf_base_path = tmp_path

            with patch("aiohttp.ClientSession") as mock_session_cls:
                mock_resp = AsyncMock()
                mock_resp.status = 200
                mock_resp.read.return_value = fake_content
                mock_resp.headers = {"Content-Type": "application/pdf"}
                mock_resp.raise_for_status = MagicMock()
                mock_resp.__aenter__ = AsyncMock(return_value=mock_resp)
                mock_resp.__aexit__ = AsyncMock(return_value=False)

                mock_get = AsyncMock()
                mock_get.__aenter__ = AsyncMock(return_value=mock_resp)
                mock_get.__aexit__ = AsyncMock(return_value=False)

                mock_session = AsyncMock()
                mock_session.get = MagicMock(return_value=mock_get)
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=False)
                mock_session_cls.return_value = mock_session

                rel_path, size_kb, fail_reason = await download_pdf(report)

        assert rel_path is not None
        assert size_kb is not None
        assert fail_reason is None
        assert (tmp_path / rel_path).exists()

    @pytest.mark.asyncio
    async def test_download_failure_returns_none(self, tmp_path):
        from storage.pdf_archiver import download_pdf
        report = make_report()

        with patch("storage.pdf_archiver.settings") as mock_settings:
            mock_settings.pdf_base_path = tmp_path

            with patch("aiohttp.ClientSession") as mock_session_cls:
                mock_session = AsyncMock()
                mock_session.__aenter__ = AsyncMock(return_value=mock_session)
                mock_session.__aexit__ = AsyncMock(return_value=False)
                mock_session.get.side_effect = Exception("connection error")
                mock_session_cls.return_value = mock_session

                rel_path, size_kb, fail_reason = await download_pdf(report)

        assert rel_path is None
        assert size_kb is None
        assert fail_reason is not None

    @pytest.mark.asyncio
    async def test_no_url_returns_none(self):
        from storage.pdf_archiver import download_pdf
        report = make_report(pdf_url=None)
        rel_path, size_kb, fail_reason = await download_pdf(report)
        assert rel_path is None
        assert fail_reason == "no_url"


class TestSafeFilename:

    def test_removes_forbidden_chars(self):
        from storage.pdf_archiver import _safe_filename
        result = _safe_filename('파일: 이름/테스트*?')
        assert ":" not in result
        assert "/" not in result
        assert "*" not in result

    def test_max_length(self):
        from storage.pdf_archiver import _safe_filename
        result = _safe_filename("a" * 100, max_len=20)
        assert len(result) <= 20


class TestIsRetryableFailure:
    """is_retryable_failure 함수: 영구 실패 vs 재시도 가능 실패 판별."""

    def test_permanent_http_404(self):
        from storage.pdf_archiver import is_retryable_failure
        assert is_retryable_failure("http_404") is False

    def test_permanent_http_410(self):
        from storage.pdf_archiver import is_retryable_failure
        assert is_retryable_failure("http_410") is False

    def test_permanent_not_pdf(self):
        from storage.pdf_archiver import is_retryable_failure
        assert is_retryable_failure("not_pdf") is False

    def test_permanent_not_pdf_html(self):
        from storage.pdf_archiver import is_retryable_failure
        assert is_retryable_failure("not_pdf:html_response") is False

    def test_permanent_no_url(self):
        from storage.pdf_archiver import is_retryable_failure
        assert is_retryable_failure("no_url") is False

    def test_permanent_unsupported_host_prefix(self):
        from storage.pdf_archiver import is_retryable_failure
        assert is_retryable_failure("unsupported_host:t.me") is False

    def test_permanent_unsupported_host_any_domain(self):
        from storage.pdf_archiver import is_retryable_failure
        assert is_retryable_failure("unsupported_host:example.com") is False

    def test_retryable_timeout(self):
        from storage.pdf_archiver import is_retryable_failure
        assert is_retryable_failure("timeout") is True

    def test_retryable_client_error(self):
        from storage.pdf_archiver import is_retryable_failure
        assert is_retryable_failure("client_error") is True

    def test_retryable_unknown(self):
        from storage.pdf_archiver import is_retryable_failure
        assert is_retryable_failure("unknown") is True

    def test_retryable_redirect_failed(self):
        from storage.pdf_archiver import is_retryable_failure
        assert is_retryable_failure("redirect_failed") is True

    def test_retryable_http_500(self):
        from storage.pdf_archiver import is_retryable_failure
        # 5xx errors are transient — should be retryable
        assert is_retryable_failure("http_500") is True
