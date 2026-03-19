"""PDF에서 차트/테이블 이미지를 추출하는 모듈.

PyMuPDF를 사용하여 텍스트 커버리지가 낮은 페이지를 이미지로 렌더링하고,
큰 임베디드 이미지를 직접 추출한다.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

# 텍스트 커버리지 임계값: 이 비율 이하면 "차트/테이블 페이지"로 간주
# 증권사 리포트 특성상 0.30 이하에 벡터 차트/재무제표가 분포
_TEXT_COVERAGE_THRESHOLD = 0.30
# 최소 이미지 크기 (px): 너무 작은 아이콘/로고 제외
_MIN_IMAGE_WIDTH = 200
_MIN_IMAGE_HEIGHT = 150
# 페이지 렌더링 DPI
_RENDER_DPI = 150
# 최대 추출 이미지 수
_MAX_IMAGES = 15


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

    try:
        for page_num in range(len(doc)):
            if len(results) >= _MAX_IMAGES:
                break

            page = doc[page_num]
            coverage = _text_coverage(page)

            if coverage <= _TEXT_COVERAGE_THRESHOLD:
                png_bytes = _render_page_to_png(page)
                results.append(ExtractedImage(
                    page_num=page_num,
                    image_bytes=png_bytes,
                    source="page_render",
                    width=int(page.rect.width * _RENDER_DPI / 72),
                    height=int(page.rect.height * _RENDER_DPI / 72),
                ))
                rendered_pages.add(page_num)
            else:
                embedded = _extract_embedded_images(page, page_num)
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
