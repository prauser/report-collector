"""PDF 다운로드 + 로컬 아카이빙."""
import re
from pathlib import Path

import aiofiles
import aiohttp
import structlog

from config.settings import settings
from db.models import Report

log = structlog.get_logger(__name__)

MAX_FILENAME_LEN = 80
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=60)


def _safe_filename(text: str, max_len: int = 20) -> str:
    """파일명에 쓸 수 없는 문자 제거 및 길이 제한."""
    text = re.sub(r'[\\/:*?"<>|\s]', "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:max_len]


def build_pdf_path(report: Report) -> Path:
    """
    상대 경로 반환: {YYYY}/{MM}/{YYYYMMDD}_{증권사}_{종목or산업}_{제목}.pdf
    """
    date = report.report_date
    year = date.strftime("%Y")
    month = date.strftime("%m")
    date_str = date.strftime("%Y%m%d")

    broker = _safe_filename(report.broker or "Unknown", 10)
    if report.stock_name:
        subject = _safe_filename(report.stock_name, 10)
    elif report.sector:
        subject = f"산업_{_safe_filename(report.sector, 10)}"
    else:
        subject = "기타"
    title_part = _safe_filename(report.title_normalized or report.title, 30)

    filename = f"{date_str}_{broker}_{subject}_{title_part}.pdf"
    return Path(year) / month / filename


async def download_pdf(report: Report) -> tuple[str | None, int | None]:
    """
    PDF 다운로드 후 로컬 저장.
    Returns: (상대경로, kb크기) or (None, None) on failure
    """
    if not report.pdf_url:
        return None, None

    rel_path = build_pdf_path(report)
    abs_path = settings.pdf_base_path / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        async with aiohttp.ClientSession(timeout=DOWNLOAD_TIMEOUT) as session:
            async with session.get(report.pdf_url) as resp:
                resp.raise_for_status()
                content = await resp.read()

        async with aiofiles.open(abs_path, "wb") as f:
            await f.write(content)

        size_kb = len(content) // 1024
        log.info("pdf_downloaded", path=str(rel_path), size_kb=size_kb)
        return str(rel_path), size_kb

    except Exception as e:
        log.warning("pdf_download_failed", url=report.pdf_url, error=str(e))
        return None, None


def get_page_count(path: Path) -> int | None:
    """PDF 페이지 수 추출 (pymupdf 설치 시)."""
    try:
        import fitz
        doc = fitz.open(str(path))
        count = doc.page_count
        doc.close()
        return count
    except Exception:
        return None


async def download_and_archive(report: Report, session) -> bool:
    """PDF 다운로드 + page_count 추출 + DB 업데이트."""
    from storage.report_repo import mark_pdf_failed, update_pdf_info

    rel_path, size_kb = await download_pdf(report)
    if not rel_path:
        await mark_pdf_failed(session, report.id)
        return False

    abs_path = settings.pdf_base_path / rel_path
    page_count = get_page_count(abs_path)

    await update_pdf_info(session, report.id, rel_path, size_kb, page_count)
    return True
