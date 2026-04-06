"""PDF 다운로드 + 로컬 아카이빙.

URL 타입별 다운로드 전략:
- DIRECT: 직접 PDF URL
- SHORT_URL: bit.ly 등 단축 URL → 리다이렉트 후 재판별
- GOOGLE_DRIVE: drive.google.com/file/d/XXX/view → direct download 변환
- UNSUPPORTED: t.me, consensus.hankyung.com 등 → 즉시 스킵
"""
import asyncio
import re
from enum import Enum
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import aiofiles
import aiohttp
import structlog

from config.settings import settings
from db.models import Report

log = structlog.get_logger(__name__)

MAX_FILENAME_LEN = 80
DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=60)
REDIRECT_TIMEOUT = aiohttp.ClientTimeout(total=15)
_TELEGRAM_DOWNLOAD_TIMEOUT = 120
_DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
}


# ──────────────────────────────────────────────
# URL 타입 판별
# ──────────────────────────────────────────────

class UrlType(str, Enum):
    DIRECT = "direct"
    SHORT_URL = "short_url"
    GOOGLE_DRIVE = "google_drive"
    UNSUPPORTED = "unsupported"


_SHORT_DOMAINS = {"bit.ly", "tinyurl.com", "goo.gl", "is.gd", "t.co", "buly.kr", "naver.me"}
_REDIRECT_DOMAINS = {"stockinfo7.com", "www.stockinfo7.com"}
_UNSUPPORTED_DOMAINS = {"t.me"}
_GDRIVE_PATTERN = re.compile(r"/file/d/([a-zA-Z0-9_-]+)")

# PDF 다운로드 실패 분류
_PERMANENT_FAILURES = {
    "http_404",
    "http_410",
    "not_pdf:html_response",
    "not_pdf",
    "no_url",
}
_PERMANENT_PREFIXES = ("unsupported_host:",)


def is_retryable_failure(reason: str) -> bool:
    """PDF 다운로드 실패 사유가 재시도 가능한지 판별."""
    if reason in _PERMANENT_FAILURES:
        return False
    for prefix in _PERMANENT_PREFIXES:
        if reason.startswith(prefix):
            return False
    return True


def detect_url_type(url: str) -> UrlType:
    """URL 호스트 기반 타입 판별."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    if host in _UNSUPPORTED_DOMAINS:
        return UrlType.UNSUPPORTED
    if host in _SHORT_DOMAINS or host in _REDIRECT_DOMAINS:
        return UrlType.SHORT_URL
    if "drive.google.com" in host:
        return UrlType.GOOGLE_DRIVE
    return UrlType.DIRECT


# ──────────────────────────────────────────────
# URL 변환 헬퍼
# ──────────────────────────────────────────────

def _gdrive_file_id(url: str) -> str | None:
    """Google Drive URL에서 file ID 추출."""
    m = _GDRIVE_PATTERN.search(url)
    return m.group(1) if m else None


def _gdrive_direct_url(url: str) -> str | None:
    """drive.google.com/file/d/XXX/view → direct download URL 변환."""
    file_id = _gdrive_file_id(url)
    if not file_id:
        return None
    return f"https://drive.google.com/uc?export=download&confirm=t&id={file_id}"


_GDRIVE_CONFIRM_PATTERN = re.compile(r'name="uuid" value="([^"]+)"')


async def _gdrive_download(session: aiohttp.ClientSession, url: str) -> bytes | None:
    """Google Drive 다운로드 — 대용량 경고 페이지 우회 포함."""
    file_id = _gdrive_file_id(url)
    if not file_id:
        return None
    dl_url = f"https://drive.google.com/uc?export=download&id={file_id}"

    async with session.get(dl_url) as resp:
        if resp.status >= 400:
            return None
        content = await resp.read()

    # 직접 PDF가 왔으면 바로 반환
    if content.startswith(b"%PDF"):
        return content

    # HTML 경고 페이지 → confirm 토큰 추출 후 재시도
    if b"virus scan" in content.lower() or b"download anyway" in content.lower() or b"uuid" in content:
        # confirm=t 방식
        confirm_url = f"https://drive.google.com/uc?export=download&confirm=t&id={file_id}"
        async with session.get(confirm_url) as resp2:
            if resp2.status >= 400:
                return None
            content2 = await resp2.read()
            if content2.startswith(b"%PDF"):
                return content2

    return None


_PDF_URL_RE = re.compile(r'https?://[^\s"\'<>]+\.pdf[^\s"\'<>]*', re.IGNORECASE)
_REPORT_URL_RE = re.compile(r'/stock/report/url/(\d+)')


def _extract_pdf_from_html(content: bytes, source_url: str) -> str | None:
    """HTML 응답에서 PDF 직접 URL을 추출. 없으면 None.

    우선순위:
    1. .pdf 확장자 URL (직접 다운로드 가능)
    2. /stock/report/url/XXXXX → stockinfo7 리다이렉트 URL로 변환
    """
    try:
        html = content.decode("utf-8", errors="replace")
    except Exception:
        return None

    # 1) 직접 PDF URL
    for m in _PDF_URL_RE.finditer(html):
        url = m.group(0)
        # Google Viewer wrapper 안의 PDF URL도 추출
        viewer_pdf = _extract_viewer_pdf_url(url)
        if viewer_pdf:
            return viewer_pdf
        # CDN/정적파일 PDF는 직접 반환
        parsed = urlparse(url)
        if parsed.hostname and parsed.hostname not in _UNSUPPORTED_DOMAINS:
            return url

    # 2) stockinfo7 report URL → 첫 번째 것으로 재시도
    report_match = _REPORT_URL_RE.search(html)
    if report_match:
        return f"https://stockinfo7.com/stock/report/url/{report_match.group(1)}"

    return None


def _extract_viewer_pdf_url(url: str) -> str | None:
    """Google Docs Viewer 등 wrapper URL에서 실제 PDF URL 추출.

    docs.google.com/viewer?url=https://...pdf → 내부 url 파라미터 반환
    """
    parsed = urlparse(url)
    if "docs.google.com" in (parsed.hostname or "") and "/viewer" in parsed.path:
        qs = parse_qs(parsed.query)
        pdf_urls = qs.get("url")
        if pdf_urls:
            return pdf_urls[0]
    return None


async def _resolve_short_url(session: aiohttp.ClientSession, url: str) -> str | None:
    """단축 URL 리다이렉트 추적 → 최종 URL 반환.

    Google Docs Viewer로 리다이렉트되면 내부 PDF URL을 추출.
    """
    resolved = None
    try:
        async with session.head(url, allow_redirects=True, timeout=REDIRECT_TIMEOUT) as resp:
            resolved = str(resp.url)
    except Exception:
        # HEAD 거부하는 서버 fallback
        try:
            async with session.get(url, allow_redirects=True, timeout=REDIRECT_TIMEOUT) as resp:
                resolved = str(resp.url)
        except Exception:
            return None

    if resolved:
        pdf_url = _extract_viewer_pdf_url(resolved)
        if pdf_url:
            log.info("viewer_pdf_extracted", original=url, viewer=resolved, pdf_url=pdf_url)
            return pdf_url
    return resolved


# ──────────────────────────────────────────────
# 파일명 빌더
# ──────────────────────────────────────────────

def _safe_filename(text: str, max_len: int = 20) -> str:
    """파일명에 쓸 수 없는 문자 제거 및 길이 제한."""
    text = re.sub(r'[\\/:*?"<>|\s]', "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text[:max_len]


def build_pdf_path(report: Report) -> Path:
    """상대 경로: {YYYY}/{MM}/{YYYYMMDD}_{증권사}_{종목or산업}_{제목}.pdf"""
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


# ──────────────────────────────────────────────
# PDF 다운로드 (URL 타입별 전략)
# ──────────────────────────────────────────────

async def download_pdf(report: Report) -> tuple[str | None, int | None, str | None]:
    """
    PDF 다운로드 후 로컬 저장.

    Returns: (상대경로, kb크기, 실패사유)
             성공: (path, kb, None)
             실패: (None, None, reason)
    """
    if not report.pdf_url:
        return None, None, "no_url"

    url = report.pdf_url
    url_type = detect_url_type(url)

    # 지원하지 않는 호스트 즉시 스킵
    if url_type == UrlType.UNSUPPORTED:
        host = urlparse(url).hostname
        log.info("pdf_unsupported_host", url=url, host=host)
        return None, None, f"unsupported_host:{host}"

    rel_path = build_pdf_path(report)
    abs_path = settings.pdf_base_path / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        async with aiohttp.ClientSession(timeout=DOWNLOAD_TIMEOUT, headers=_DEFAULT_HEADERS) as session:
            download_url = url

            # 단축 URL → 리다이렉트 추적
            if url_type == UrlType.SHORT_URL:
                resolved = await _resolve_short_url(session, url)
                if not resolved:
                    return None, None, "redirect_failed"
                download_url = resolved
                # 리다이렉트 결과 재판별
                resolved_type = detect_url_type(resolved)
                if resolved_type == UrlType.UNSUPPORTED:
                    host = urlparse(resolved).hostname
                    return None, None, f"unsupported_host:{host}"
                if resolved_type == UrlType.GOOGLE_DRIVE:
                    url_type = UrlType.GOOGLE_DRIVE  # 이하 gdrive 경로 사용
                    download_url = resolved

            # Google Drive → 대용량 경고 우회 포함 다운로드
            if url_type == UrlType.GOOGLE_DRIVE:
                content = await _gdrive_download(session, download_url)
                if content and content.startswith(b"%PDF"):
                    async with aiofiles.open(abs_path, "wb") as f:
                        await f.write(content)
                    size_kb = len(content) // 1024
                    log.info("pdf_downloaded", path=str(rel_path), size_kb=size_kb, url_type="google_drive")
                    return str(rel_path), size_kb, None
                return None, None, "gdrive_download_failed"

            # 일반 다운로드
            async with session.get(download_url) as resp:
                if resp.status >= 400:
                    return None, None, f"http_{resp.status}"
                content = await resp.read()

            # PDF magic bytes 검증
            if not content.startswith(b"%PDF"):
                ct = resp.headers.get("Content-Type", "")
                # HTML fallback: PDF 링크를 추출해서 재시도
                if "text/html" in ct:
                    fallback_url = _extract_pdf_from_html(content, download_url)
                    if fallback_url:
                        log.info("html_fallback_pdf", original=download_url, extracted=fallback_url)
                        async with session.get(fallback_url) as resp2:
                            if resp2.status < 400:
                                content2 = await resp2.read()
                                if content2.startswith(b"%PDF"):
                                    async with aiofiles.open(abs_path, "wb") as f:
                                        await f.write(content2)
                                    size_kb = len(content2) // 1024
                                    log.info("pdf_downloaded", path=str(rel_path), size_kb=size_kb, url_type="html_fallback")
                                    return str(rel_path), size_kb, None
                log.warning(
                    "pdf_invalid_content",
                    url=download_url,
                    original_url=url,
                    content_type=ct,
                    size=len(content),
                )
                if "text/html" in ct:
                    return None, None, "not_pdf:html_response"
                return None, None, "not_pdf"

            async with aiofiles.open(abs_path, "wb") as f:
                await f.write(content)

            size_kb = len(content) // 1024
            log.info("pdf_downloaded", path=str(rel_path), size_kb=size_kb, url_type=url_type.value)
            return str(rel_path), size_kb, None

    except asyncio.TimeoutError:
        log.warning("pdf_timeout", url=url)
        return None, None, "timeout"
    except aiohttp.ClientError as e:
        log.warning("pdf_client_error", url=url, error=str(e))
        return None, None, "client_error"
    except Exception as e:
        log.warning("pdf_download_failed", url=url, error=str(e))
        return None, None, "unknown"


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


# ──────────────────────────────────────────────
# 텔레그램 Document 다운로드
# ──────────────────────────────────────────────

_TME_MSG_RE = re.compile(r"https?://(?:t\.me|telegram\.me)/([a-zA-Z_]\w+)/(\d+)")


async def resolve_tme_links(client, links: list[str]) -> tuple[str | None, object | None]:
    """t.me 메시지 링크에서 PDF URL 또는 document 메시지 반환.

    Returns: (pdf_url, message_with_document)
      - pdf_url이 발견되면 해당 URL 반환 (message_with_document는 None)
      - document PDF가 있으면 (None, message) 반환
      - 둘 다 없으면 (None, None) 반환
    """
    from telethon.tl.types import MessageMediaDocument, DocumentAttributeFilename

    for link in links:
        m = _TME_MSG_RE.match(link)
        if not m:
            continue
        channel_name, msg_id = m.group(1), int(m.group(2))
        try:
            msg = await client.get_messages(channel_name, ids=msg_id)
        except Exception as e:
            log.debug("tme_resolve_failed", link=link, error=str(e))
            continue
        if not msg:
            continue

        # 1) 메시지에 PDF document 첨부가 있는 경우
        if isinstance(getattr(msg, "media", None), MessageMediaDocument):
            doc = msg.media.document
            if "pdf" in getattr(doc, "mime_type", ""):
                log.info("tme_resolved_document", link=link)
                return None, msg

        # 2) 메시지 텍스트에서 PDF URL 추출
        text = msg.text or ""
        if text:
            # 중첩 t.me 링크 추적 (향후 재귀 resolve 필요 여부 판단용)
            nested_tme = _TME_MSG_RE.findall(text)
            if nested_tme:
                log.warning("tme_nested_link_found", link=link,
                            nested=[f"t.me/{ch}/{mid}" for ch, mid in nested_tme])

            # .pdf 확장자 URL 우선
            pdf_match = re.search(r"https?://\S+\.pdf[^\s)]*", text, re.IGNORECASE)
            if pdf_match:
                url = pdf_match.group(0)
                host = urlparse(url).hostname or ""
                if host not in _UNSUPPORTED_DOMAINS:
                    log.info("tme_resolved_pdf_url", link=link, url=url)
                    return url, None
            # 일반 URL fallback
            for url_match in re.finditer(r"https?://\S+", text):
                url = url_match.group(0)
                host = urlparse(url).hostname or ""
                if host not in _UNSUPPORTED_DOMAINS and host not in {"t.me", "telegram.me"}:
                    log.info("tme_resolved_url", link=link, url=url)
                    return url, None

    return None, None


async def download_telegram_document(client, message, report: Report) -> tuple[str | None, int | None]:
    """텔레그램 Document (PDF) 를 Telethon으로 직접 다운로드."""
    rel_path = build_pdf_path(report)
    abs_path = settings.pdf_base_path / rel_path
    abs_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        await asyncio.wait_for(
            client.download_media(message, file=str(abs_path)),
            timeout=_TELEGRAM_DOWNLOAD_TIMEOUT,
        )
        size_kb = abs_path.stat().st_size // 1024
        log.info("telegram_pdf_downloaded", path=str(rel_path), size_kb=size_kb)
        return str(rel_path), size_kb
    except asyncio.TimeoutError:
        log.warning("telegram_download_timeout", report_id=report.id, timeout=_TELEGRAM_DOWNLOAD_TIMEOUT)
        abs_path.unlink(missing_ok=True)
        return None, None
    except Exception as e:
        log.warning("telegram_pdf_download_failed", error=str(e))
        return None, None


# ──────────────────────────────────────────────
# 통합 헬퍼
# ──────────────────────────────────────────────

async def download_and_archive(report: Report, session) -> bool:
    """PDF 다운로드 + page_count 추출 + DB 업데이트."""
    from storage.report_repo import mark_pdf_failed, update_pdf_info

    rel_path, size_kb, fail_reason = await download_pdf(report)
    if not rel_path:
        await mark_pdf_failed(session, report.id, fail_reason or "unknown")
        await session.commit()
        return False

    abs_path = settings.pdf_base_path / rel_path
    page_count = get_page_count(abs_path)

    await update_pdf_info(session, report.id, rel_path, size_kb, page_count)
    await session.commit()
    return True


# ──────────────────────────────────────────────
# PDF 파일명 헬퍼 (listener/backfill 공유)
# ──────────────────────────────────────────────

def pdf_filename(message) -> str | None:
    """Document 타입 PDF 메시지에서 파일명 추출. PDF가 아니면 None.

    telethon MessageMediaDocument + DocumentAttributeFilename 기반.
    listener.py, backfill.py에서 공통으로 사용.
    """
    try:
        from telethon.tl.types import MessageMediaDocument, DocumentAttributeFilename
    except ImportError:
        return None
    if not isinstance(message.media, MessageMediaDocument):
        return None
    doc = message.media.document
    if "pdf" not in getattr(doc, "mime_type", ""):
        return None
    for attr in doc.attributes or []:
        if isinstance(attr, DocumentAttributeFilename):
            return attr.file_name
    return None


# ──────────────────────────────────────────────
# 3-stage PDF fallback orchestration
# ──────────────────────────────────────────────

async def attempt_pdf_download(
    client,
    report: Report,
    message=None,
    tme_links: list[str] | None = None,
    session=None,
    telegram_sem=None,
    http_sem=None,
) -> tuple[bool, str | None, int | None, str | None, bool | None]:
    """3-stage PDF fallback: Telegram doc → t.me resolve → HTTP download.

    Stage 1: If `message` is provided (or report.source_message_id allows fetching),
             try Telegram document download directly.
    Stage 2: If tme_links provided, resolve t.me links → PDF URL or document.
    Stage 3: HTTP download from report.pdf_url.

    Args:
        client: Telethon client
        report: Report ORM object (needs .pdf_url, .source_message_id, etc.)
        message: Optional original Telethon message object (for Stage 1 direct download)
        tme_links: Optional list of t.me message link strings (for Stage 2)
        session: Optional SQLAlchemy async session (if provided, DB updates are done inside)
        telegram_sem: Optional asyncio.Semaphore for throttling Telegram calls (None = no limit)
        http_sem: Optional asyncio.Semaphore for throttling HTTP calls (None = no limit)

    Returns:
        (success, pdf_path_or_none, size_kb_or_none, fail_reason_or_none, fail_retryable_or_none)
        On success: (True, rel_path, size_kb, None, None)
        On failure: (False, None, None, reason_str, is_retryable_bool)
    """
    from storage.report_repo import update_pdf_info, mark_pdf_failed, update_pipeline_status

    attempts = []
    any_permanent = False  # tracks if any stage had a permanent (non-retryable) failure

    async def _do_telegram_download(msg, label="telegram"):
        """Attempt telegram document download, respecting telegram_sem."""
        if telegram_sem is not None:
            async with telegram_sem:
                return await download_telegram_document(client, msg, report)
        else:
            return await download_telegram_document(client, msg, report)

    async def _do_http_download():
        """Attempt HTTP download, respecting http_sem."""
        if http_sem is not None:
            async with http_sem:
                return await download_pdf(report)
        else:
            return await download_pdf(report)

    # --- Stage 1: Direct Telegram document (original message) ---
    if message is not None:
        rel_path, size_kb = await _do_telegram_download(message)
        if rel_path:
            if session is not None:
                await update_pdf_info(session, report.id, rel_path, size_kb, None)
                await update_pipeline_status(session, report.id, "pdf_done")
                await session.commit()
            return True, rel_path, size_kb, None, None
        attempts.append("telegram_doc: download_failed")

    # --- Stage 2: t.me link resolution ---
    if tme_links and not report.pdf_url and not report.pdf_path:
        try:
            if telegram_sem is not None:
                async with telegram_sem:
                    tme_url, tme_msg = await resolve_tme_links(client, tme_links)
            else:
                tme_url, tme_msg = await resolve_tme_links(client, tme_links)

            if tme_url:
                # Update pdf_url on report object and (optionally) in DB
                report.pdf_url = tme_url
                if session is not None:
                    from sqlalchemy import update as sa_update
                    from db.models import Report as ReportModel
                    await session.execute(
                        sa_update(ReportModel).where(ReportModel.id == report.id)
                        .values(pdf_url=tme_url)
                    )
                # Fall through to Stage 3
            elif tme_msg:
                rel_path, size_kb = await _do_telegram_download(tme_msg, "tme_doc")
                if rel_path:
                    if session is not None:
                        await update_pdf_info(session, report.id, rel_path, size_kb, None)
                        await update_pipeline_status(session, report.id, "pdf_done")
                        await session.commit()
                    return True, rel_path, size_kb, None, None
                attempts.append("tme_document: download_failed")
            else:
                attempts.append(f"tme_resolve: no_result ({len(tme_links)} links)")
        except Exception as e:
            attempts.append(f"tme_resolve: {e}")

    # --- Stage 3: HTTP download from pdf_url ---
    if report.pdf_url and not report.pdf_path:
        rel_path, size_kb, fail_reason = await _do_http_download()
        if rel_path:
            if session is not None:
                await update_pdf_info(session, report.id, rel_path, size_kb, None)
                await update_pipeline_status(session, report.id, "pdf_done")
                await session.commit()
            return True, rel_path, size_kb, None, None
        if fail_reason and not is_retryable_failure(fail_reason):
            any_permanent = True
        attempts.append(f"url_download: {fail_reason}")

    # --- All stages failed ---
    if not message and not (tme_links) and not report.pdf_url:
        reason = "no_source"
        retryable = False
        if session is not None:
            await mark_pdf_failed(session, report.id, reason)
            await session.commit()
        return False, None, None, reason, retryable

    fail_detail = " | ".join(attempts) if attempts else "unknown"
    fail_detail = fail_detail[:500]
    # If any individual stage had a permanent failure, the overall result is non-retryable
    retryable = not any_permanent and is_retryable_failure(fail_detail)

    if session is not None:
        await mark_pdf_failed(session, report.id, fail_detail)
        await session.commit()

    return False, None, None, fail_detail, retryable
