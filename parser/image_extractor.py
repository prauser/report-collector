"""PDF에서 차트/테이블 이미지를 추출하는 모듈.

PyMuPDF를 사용하여 다중 시그널(벡터 밀도, 임베드 이미지 크기, 페이지 인덱스,
키워드 가산점)로 차트/테이블 가능성을 점수화하고, 임계점 이상 페이지만
렌더링/추출한다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_TEXT_COVERAGE_THRESHOLD = 0.30
_MIN_IMAGE_WIDTH = 200
_MIN_IMAGE_HEIGHT = 150
_RENDER_DPI = 150
_MAX_IMAGES = 15

# 표지/면책 자동 스킵
_HARD_SKIP_FIRST_N = 1
_HARD_SKIP_LAST_N = 1

# 시그널 스코어링 임계값
_SCORE_THRESHOLD = 3
_VECTOR_DENSITY_THRESHOLD = 50    # 차트는 보통 50+ vector 객체
_LARGE_IMAGE_RATIO = 0.5          # 페이지 폭 50%+ 이미지면 차트 가능성↑
_HEADER_FONT_SIZE = 11            # 이 폰트 이상이면 섹션 헤더로 간주

_VALUATION_PATTERN = re.compile(
    r"\b(PER|PBR|EV/EBITDA|EV/Sales|ROE|ROIC|Valuation|Forecast|EPS|BPS|"
    r"Earnings|Target\s*Price)\b"
    r"|밸류에이션|추정|전망|목표주가|적정주가|매출액|영업이익|분기실적|"
    r"연간실적|Financial\s*Data",
    re.IGNORECASE,
)


def _has_keyword(text: str) -> bool:
    return bool(text and _VALUATION_PATTERN.search(text))


@dataclass
class ExtractedImage:
    """추출된 이미지 정보."""
    page_num: int          # 0-based
    image_bytes: bytes     # PNG 바이트
    source: str            # "page_render" | "embedded"
    width: int
    height: int


def _text_coverage(page) -> float:
    """페이지 면적 대비 텍스트가 차지하는 비율."""
    page_rect = page.rect
    page_area = page_rect.width * page_rect.height
    if page_area == 0:
        return 0.0

    blocks = page.get_text("blocks")
    text_area = 0.0
    for b in blocks:
        # blocks: (x0, y0, x1, y1, text, block_no, block_type)
        # block_type 0 = text
        if b[6] == 0 and b[4].strip():
            w = b[2] - b[0]
            h = b[3] - b[1]
            text_area += w * h

    return text_area / page_area


def _vector_count(page) -> int:
    try:
        return len(page.get_drawings())
    except Exception:
        return 0


def _largest_image_ratio(page) -> float:
    """페이지 폭 대비 가장 큰 임베드 이미지의 폭 비율."""
    page_w = page.rect.width or 1.0
    largest = 0.0
    try:
        for img in page.get_images(full=True):
            xref = img[0]
            for r in page.get_image_rects(xref):
                ratio = r.width / page_w
                if ratio > largest:
                    largest = ratio
    except Exception:
        pass
    return largest


def _section_header_has_keyword(page) -> bool:
    """헤더급 폰트 텍스트(섹션 제목)에 valuation 키워드가 있는지."""
    try:
        d = page.get_text("dict")
    except Exception:
        return False
    for block in d.get("blocks", []):
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                if span.get("size", 0) >= _HEADER_FONT_SIZE and _has_keyword(span.get("text", "")):
                    return True
    return False


@dataclass
class _PageSignals:
    page_idx: int
    text_coverage: float
    vector_count: int
    largest_image_ratio: float
    has_keyword: bool
    section_header_keyword: bool


def _collect_signals(doc) -> list[_PageSignals]:
    out: list[_PageSignals] = []
    for i in range(len(doc)):
        page = doc[i]
        text = page.get_text() or ""
        out.append(_PageSignals(
            page_idx=i,
            text_coverage=_text_coverage(page),
            vector_count=_vector_count(page),
            largest_image_ratio=_largest_image_ratio(page),
            has_keyword=_has_keyword(text),
            section_header_keyword=_section_header_has_keyword(page),
        ))
    return out


def _score_page(s: _PageSignals, total: int, neighbor_kw: bool) -> int:
    # 표지·면책 컷 (단, 짧은 PDF는 모든 페이지가 의미 있을 수 있어 스킵 면제)
    if total > 3:
        if s.page_idx < _HARD_SKIP_FIRST_N:
            return 0
        if s.page_idx >= total - _HARD_SKIP_LAST_N:
            return 0
    score = 0
    if s.has_keyword:
        score += 3
    if s.section_header_keyword:
        score += 2
    if s.vector_count >= _VECTOR_DENSITY_THRESHOLD:
        score += 2
    if s.largest_image_ratio >= _LARGE_IMAGE_RATIO:
        score += 2
    if neighbor_kw:
        score += 1
    return score


def _render_page_to_png(page, dpi: int = _RENDER_DPI) -> bytes:
    """페이지를 PNG 바이트로 렌더링."""
    mat = __import__("pymupdf").Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat)
    return pix.tobytes("png")


def _extract_embedded_images(page, page_num: int) -> list[ExtractedImage]:
    """페이지에서 큰 임베디드 이미지를 직접 추출."""
    import pymupdf

    results = []
    image_list = page.get_images(full=True)

    for img_info in image_list:
        xref = img_info[0]
        try:
            base_image = pymupdf.Pixmap(page.parent, xref)
        except Exception:
            continue

        w, h = base_image.width, base_image.height
        if w < _MIN_IMAGE_WIDTH or h < _MIN_IMAGE_HEIGHT:
            base_image = None
            continue

        # CMYK → RGB 변환
        if base_image.n > 4:
            base_image = pymupdf.Pixmap(pymupdf.csRGB, base_image)

        results.append(ExtractedImage(
            page_num=page_num,
            image_bytes=base_image.tobytes("png"),
            source="embedded",
            width=w,
            height=h,
        ))
        base_image = None

    return results


_IMAGE_EXTRACT_TIMEOUT = 30


def _extract_images_sync(pdf_path: Path) -> list[ExtractedImage]:
    """동기 이미지 추출 (to_thread용)."""
    import pymupdf

    try:
        doc = pymupdf.open(pdf_path)
    except Exception as e:
        log.warning("pdf_open_failed", path=str(pdf_path), error=str(e))
        return []

    results: list[ExtractedImage] = []
    rendered_pages: set[int] = set()
    skipped_low_score = 0

    try:
        signals = _collect_signals(doc)
        total = len(signals)
        keyword_pages = {
            s.page_idx for s in signals
            if s.has_keyword or s.section_header_keyword
        }

        for s in signals:
            if len(results) >= _MAX_IMAGES:
                break

            neighbor_kw = (
                (s.page_idx - 1) in keyword_pages
                or (s.page_idx + 1) in keyword_pages
            )
            score = _score_page(s, total, neighbor_kw)
            if score < _SCORE_THRESHOLD:
                skipped_low_score += 1
                continue

            page = doc[s.page_idx]
            # 페이지 통째 렌더 vs 임베드 추출 분기
            if s.text_coverage <= _TEXT_COVERAGE_THRESHOLD or s.largest_image_ratio >= _LARGE_IMAGE_RATIO:
                png_bytes = _render_page_to_png(page)
                results.append(ExtractedImage(
                    page_num=s.page_idx,
                    image_bytes=png_bytes,
                    source="page_render",
                    width=int(page.rect.width * _RENDER_DPI / 72),
                    height=int(page.rect.height * _RENDER_DPI / 72),
                ))
                rendered_pages.add(s.page_idx)
            else:
                embedded = _extract_embedded_images(page, s.page_idx)
                remaining = _MAX_IMAGES - len(results)
                results.extend(embedded[:remaining])
    finally:
        doc.close()

    log.info(
        "images_extracted",
        path=str(pdf_path),
        total=len(results),
        page_renders=len(rendered_pages),
        embedded=len([r for r in results if r.source == "embedded"]),
        skipped_low_score=skipped_low_score,
    )
    return results


async def extract_images_from_pdf(pdf_path: Path) -> list[ExtractedImage]:
    """PDF에서 차트/테이블 이미지를 추출. 타임아웃 적용."""
    import asyncio

    try:
        import pymupdf  # noqa: F401
    except ImportError:
        log.error("pymupdf_not_installed")
        return []

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_extract_images_sync, pdf_path),
            timeout=_IMAGE_EXTRACT_TIMEOUT,
        )
    except asyncio.TimeoutError:
        log.warning("image_extract_timeout", path=str(pdf_path), timeout=_IMAGE_EXTRACT_TIMEOUT)
        return []
    except Exception as e:
        log.warning("image_extract_failed", path=str(pdf_path), error=str(e))
        return []
