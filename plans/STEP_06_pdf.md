# STEP 06 — PDF 아카이빙

## 목표
- PDF URL → 로컬 다운로드 + 경로 DB 저장
- Telethon 첨부파일 다운로드 (PDF URL 없는 경우)
- 실패한 PDF 재시도 스크립트

## 사전 조건
- STEP 05 완료
- `data/pdfs/` 디렉토리 존재 (또는 config.pdf_base_path)

## 구현 대상

### storage/pdf_archiver.py 보완

페이지 수 추출 (pymupdf 설치 시):

```python
def get_page_count(path: Path) -> int | None:
    try:
        import fitz
        doc = fitz.open(str(path))
        count = doc.page_count
        doc.close()
        return count
    except Exception:
        return None
```

다운로드 후 자동으로 page_count 추출:

```python
async def download_and_archive(report: Report, session: AsyncSession) -> bool:
    rel_path, size_kb = await download_pdf(report)
    if not rel_path:
        await mark_pdf_failed(session, report.id)
        return False

    abs_path = settings.pdf_base_path / rel_path
    page_count = get_page_count(abs_path)

    await update_pdf_info(session, report.id, rel_path, size_kb, page_count)
    return True
```

### collector/listener.py 에 Telethon 첨부파일 처리 추가

```python
# message.document가 있고 PDF인 경우
if message.document:
    mime = getattr(message.document, "mime_type", "")
    if "pdf" in mime:
        # Telethon으로 직접 다운로드
        rel_path = build_pdf_path(report)
        abs_path = settings.pdf_base_path / rel_path
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        await client.download_media(message.document, file=str(abs_path))
        size_kb = abs_path.stat().st_size // 1024
        await update_pdf_info(session, report.id, str(rel_path), size_kb, None)
```

### scripts/retry_pdf.py (신규)

```python
"""pdf_download_failed=True인 레코드 재시도."""
import asyncio
from db.session import AsyncSessionLocal
from storage.report_repo import get_reports_needing_pdf, mark_pdf_failed
from storage.pdf_archiver import download_and_archive

async def retry_failed_pdfs(limit: int = 50) -> None:
    async with AsyncSessionLocal() as session:
        reports = await get_reports_needing_pdf(session, limit)

    for report in reports:
        async with AsyncSessionLocal() as session:
            await download_and_archive(report, session)

if __name__ == "__main__":
    asyncio.run(retry_failed_pdfs())
```

## 테스트 코드

### tests/test_pdf_archiver.py

```python
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
        assert str(path).startswith("2026/03/")
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
        # 파일명에 :, / 없어야 함
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
                mock_resp.read.return_value = fake_content
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

                rel_path, size_kb = await download_pdf(report)

        assert rel_path is not None
        assert size_kb is not None
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

                rel_path, size_kb = await download_pdf(report)

        assert rel_path is None
        assert size_kb is None


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
```

### 실행

```bash
pip install pytest-asyncio aiofiles aiohttp
pytest tests/test_pdf_archiver.py -v
```

## 검증 체크리스트

- [ ] `build_pdf_path()` - 올바른 경로 생성
- [ ] 다운로드 성공 시 파일 생성 + DB 업데이트
- [ ] 다운로드 실패 시 `pdf_download_failed=True`
- [ ] 파일명에 특수문자 없음
- [ ] 재시도 스크립트 동작
- [ ] pytest 모두 PASS

## 완료 기준 → STEP 07 진입

체크리스트 통과 시.

## 이슈/메모

- 한경컨센서스 PDF URL은 단기 만료될 수 있음 → 수집 즉시 다운로드 권장
- 동일 파일명 충돌 가능성: DB id를 파일명에 suffix로 붙이는 것도 고려
- pymupdf는 선택 설치. 없으면 page_count=None으로 처리
