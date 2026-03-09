"""PDF 본문 LLM 분석 — ai_summary, ai_sentiment, ai_keywords 추출."""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import structlog
from anthropic import AsyncAnthropic, RateLimitError, APIConnectionError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config.settings import settings
from db.models import Report
from storage.llm_usage_repo import record_llm_usage

log = structlog.get_logger(__name__)

# PDF 텍스트 최대 길이 (토큰 절약: ~4000 tokens 분량)
PDF_TEXT_MAX_CHARS = 12_000

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            timeout=settings.llm_timeout,
        )
    return _client


SYSTEM_PROMPT = """\
당신은 한국 증권사 리서치 리포트 분석 전문가입니다.
PDF에서 추출된 리포트 본문을 읽고 핵심 내용을 분석합니다.
"""

ANALYZE_TOOL = {
    "name": "analyze_report",
    "description": "증권사 리포트 본문을 분석하여 요약, 감성, 키워드를 추출합니다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "리포트 핵심 내용 요약 (2~4문장). 투자의견, 목표주가, 주요 근거 포함.",
            },
            "sentiment": {
                "type": "number",
                "description": (
                    "강세/약세 감성 점수. "
                    "1.0=매우 긍정(강력매수), 0.5=긍정(매수), 0.0=중립, "
                    "-0.5=부정(매도), -1.0=매우 부정(강력매도)."
                ),
            },
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "리포트 핵심 키워드 5~10개 (업종, 이슈, 재무지표 등).",
            },
        },
        "required": ["summary", "sentiment", "keywords"],
    },
}


def extract_pdf_text(pdf_path: Path) -> str | None:
    """pypdf로 PDF 텍스트 추출. 최대 PDF_TEXT_MAX_CHARS 자."""
    try:
        from pypdf import PdfReader
        reader = PdfReader(str(pdf_path))
        parts = []
        total = 0
        for page in reader.pages:
            text = page.extract_text() or ""
            parts.append(text)
            total += len(text)
            if total >= PDF_TEXT_MAX_CHARS:
                break
        full = "\n".join(parts)
        return full[:PDF_TEXT_MAX_CHARS] if full.strip() else None
    except Exception as e:
        log.warning("pdf_text_extract_failed", path=str(pdf_path), error=str(e))
        return None


@retry(
    stop=stop_after_attempt(settings.llm_max_retries + 1),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((RateLimitError, APIConnectionError)),
    reraise=True,
)
async def _call_llm(pdf_text: str, report_meta: str):
    """LLM 호출 → (결과 dict, response) 반환."""
    client = _get_client()
    user_content = f"[리포트 메타]\n{report_meta}\n\n[PDF 본문]\n{pdf_text}"
    response = await client.messages.create(
        model=settings.llm_model,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        tools=[ANALYZE_TOOL],
        tool_choice={"type": "tool", "name": "analyze_report"},
        messages=[{"role": "user", "content": user_content}],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "analyze_report":
            return block.input, response
    return None, response


async def analyze_pdf(report: Report) -> dict | None:
    """
    리포트의 PDF를 분석하여 (summary, sentiment, keywords) 반환.
    실패 시 None 반환 (호출자가 fallback 처리).
    """
    if not settings.llm_enabled or not settings.anthropic_api_key:
        return None

    if not report.pdf_path:
        return None

    abs_path = settings.pdf_base_path / report.pdf_path
    if not abs_path.exists():
        log.warning("pdf_file_not_found", path=str(abs_path), report_id=report.id)
        return None

    pdf_text = extract_pdf_text(abs_path)
    if not pdf_text:
        log.warning("pdf_text_empty", report_id=report.id)
        return None

    report_meta = (
        f"증권사: {report.broker or '-'}\n"
        f"종목: {report.stock_name or '-'}\n"
        f"제목: {report.title}\n"
        f"투자의견: {report.opinion or '-'}\n"
        f"목표주가: {report.target_price or '-'}"
    )

    try:
        result, response = await _call_llm(pdf_text, report_meta)
    except Exception as e:
        log.warning("pdf_llm_call_failed", report_id=report.id, error=str(e))
        return None

    usage = response.usage
    await record_llm_usage(
        model=settings.llm_model,
        purpose="pdf_analysis",
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        source_channel=report.source_channel,
        report_id=report.id,
    )

    if result is None:
        log.warning("pdf_llm_no_result", report_id=report.id)
        return None

    # sentiment 범위 클리핑 (-1.0 ~ 1.0)
    sentiment = result.get("sentiment", 0.0)
    sentiment = max(-1.0, min(1.0, float(sentiment)))

    analysis = {
        "summary": result.get("summary", ""),
        "sentiment": Decimal(str(round(sentiment, 2))),
        "keywords": result.get("keywords", []),
    }

    log.info(
        "pdf_analyzed",
        report_id=report.id,
        sentiment=sentiment,
        keywords=analysis["keywords"][:3],
    )
    return analysis
