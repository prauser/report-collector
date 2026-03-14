"""PDF → Markdown 변환 모듈.

PyMuPDF4LLM을 기본으로 사용하며, 향후 Marker 등으로 교체 가능.
"""
from __future__ import annotations

from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)


def _estimate_token_count(text: str) -> int:
    """대략적 토큰 수 추정 (한국어 기준 ~1.5자/토큰)."""
    return len(text) * 2 // 3


async def convert_pdf_to_markdown(pdf_path: Path) -> tuple[str | None, str]:
    """
    PDF를 Markdown으로 변환.

    Returns:
        (markdown_text, converter_name)
        변환 실패 시 (None, converter_name)
    """
    converter = settings.markdown_converter

    if converter == "pymupdf4llm":
        return await _convert_pymupdf4llm(pdf_path), converter
    elif converter == "marker":
        return await _convert_marker(pdf_path), converter
    else:
        log.warning("unknown_converter", converter=converter)
        return await _convert_pymupdf4llm(pdf_path), "pymupdf4llm"


async def _convert_pymupdf4llm(pdf_path: Path) -> str | None:
    """PyMuPDF4LLM으로 PDF → Markdown 변환."""
    try:
        import pymupdf4llm

        md_text = pymupdf4llm.to_markdown(str(pdf_path))
        if not md_text or not md_text.strip():
            log.warning("pymupdf4llm_empty", path=str(pdf_path))
            return None
        return md_text
    except ImportError:
        log.error("pymupdf4llm_not_installed")
        return await _convert_fallback(pdf_path)
    except Exception as e:
        log.warning("pymupdf4llm_failed", path=str(pdf_path), error=str(e))
        return await _convert_fallback(pdf_path)


async def _convert_marker(pdf_path: Path) -> str | None:
    """Marker로 PDF → Markdown 변환 (향후 구현)."""
    log.warning("marker_not_implemented", path=str(pdf_path))
    return await _convert_pymupdf4llm(pdf_path)


async def _convert_fallback(pdf_path: Path) -> str | None:
    """pypdf 기반 단순 텍스트 추출 (fallback)."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(pdf_path))
        parts = []
        for page in reader.pages:
            text = page.extract_text() or ""
            parts.append(text)
        full = "\n\n".join(parts)
        return full if full.strip() else None
    except Exception as e:
        log.warning("fallback_convert_failed", path=str(pdf_path), error=str(e))
        return None
