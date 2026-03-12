"""LLM 파서 — S2a(분류) + S2b(메타데이터 추출) 2단계."""
from __future__ import annotations

import structlog
from anthropic import AsyncAnthropic, RateLimitError, APIConnectionError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config.settings import settings
from parser.base import ParsedReport
from parser.normalizer import normalize_broker, normalize_opinion, parse_price
from storage.llm_usage_repo import record_llm_usage

log = structlog.get_logger(__name__)

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            timeout=settings.llm_timeout,
        )
    return _client


# ──────────────────────────────────────────────
# S2a: 분류 전용 (경량 스키마 — 토큰 절약)
# ──────────────────────────────────────────────

_S2A_SYSTEM = """\
당신은 텔레그램 채널 메시지가 증권사 리포트인지 판별하는 전문가입니다.
메시지를 읽고 아래 중 하나로 분류하세요.

- broker_report: 증권사가 발행한 종목/산업 리서치 리포트
- news: 뉴스 기사, 시황, 공시, 주가 알림
- general: 광고, 채널 안내, 잡담, 이모지 도배 등
- ambiguous: 리포트일 수도 있지만 판단하기 어려운 경우

broker_report와 ambiguous의 차이:
- PDF 링크 또는 증권사명 + 종목명 조합이 명확하면 broker_report
- 텍스트가 손상/잘림/외국어로 되어 있거나 PDF만 있고 증권사를 전혀 알 수 없으면 ambiguous
"""

_S2A_TOOL = {
    "name": "classify_message",
    "description": "텔레그램 메시지 유형을 분류합니다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "message_type": {
                "type": "string",
                "enum": ["broker_report", "news", "general", "ambiguous"],
                "description": "메시지 유형",
            },
            "reason": {
                "type": "string",
                "description": "ambiguous일 때만 — 판단이 어려운 이유 (1문장)",
            },
        },
        "required": ["message_type"],
    },
}


@retry(
    stop=stop_after_attempt(settings.llm_max_retries + 1),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((RateLimitError, APIConnectionError)),
    reraise=True,
)
async def _call_s2a(message_text: str):
    client = _get_client()
    response = await client.messages.create(
        model=settings.llm_model,
        max_tokens=128,
        system=_S2A_SYSTEM,
        tools=[_S2A_TOOL],
        tool_choice={"type": "tool", "name": "classify_message"},
        messages=[{"role": "user", "content": message_text}],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "classify_message":
            return block.input, response
    return None, response


# ──────────────────────────────────────────────
# S2b: 메타데이터 추출 전용
# ──────────────────────────────────────────────

_S2B_SYSTEM = """\
당신은 한국 증권사 리포트 메타데이터 추출 전문가입니다.
broker_report로 확정된 텔레그램 메시지와 PDF 메타데이터를 분석하여
정확한 메타데이터를 추출하세요.
마크다운 링크([]()) 안의 텍스트, 볼드(**) 등 서식은 무시하고 실제 내용만 추출하세요.
"""

_S2B_TOOL = {
    "name": "extract_metadata",
    "description": "증권사 리포트 메타데이터를 추출합니다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "broker": {
                "type": "string",
                "description": "증권사명 (예: 미래에셋증권, KB증권)",
            },
            "stock_name": {
                "type": "string",
                "description": "종목명 (예: 삼성전자). 종목 리포트일 때만.",
            },
            "stock_code": {
                "type": "string",
                "description": "종목코드 6자리 (예: 005930). 알 수 있을 때만.",
            },
            "title": {
                "type": "string",
                "description": "리포트 제목. 마크다운 서식 제거한 순수 텍스트.",
            },
            "analyst": {
                "type": "string",
                "description": "애널리스트 이름.",
            },
            "opinion": {
                "type": "string",
                "description": "투자의견 (매수, 중립, 매도, 비중확대 등).",
            },
            "target_price": {
                "type": "string",
                "description": "목표주가 (예: 85,000원, 8.5만원).",
            },
            "prev_target_price": {
                "type": "string",
                "description": "이전 목표주가.",
            },
            "prev_opinion": {
                "type": "string",
                "description": "이전 투자의견.",
            },
            "sector": {
                "type": "string",
                "description": "섹터/산업 (예: 반도체, 자동차). 산업 리포트일 때만.",
            },
            "report_type": {
                "type": "string",
                "description": "리포트 유형 (기업분석, 산업분석, 실적리뷰, 기업메모 등).",
            },
        },
        "required": [],
    },
}


@retry(
    stop=stop_after_attempt(settings.llm_max_retries + 1),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((RateLimitError, APIConnectionError)),
    reraise=True,
)
async def _call_s2b(message_text: str, pdf_meta_context: str | None):
    client = _get_client()
    user_content = message_text
    if pdf_meta_context:
        user_content = f"[PDF 메타데이터]\n{pdf_meta_context}\n\n[메시지]\n{message_text}"
    response = await client.messages.create(
        model=settings.llm_model,
        max_tokens=512,
        system=_S2B_SYSTEM,
        tools=[_S2B_TOOL],
        tool_choice={"type": "tool", "name": "extract_metadata"},
        messages=[{"role": "user", "content": user_content}],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "extract_metadata":
            return block.input, response
    return None, response


# ──────────────────────────────────────────────
# 공개 인터페이스
# ──────────────────────────────────────────────

class S2aResult:
    """S2a 분류 결과."""
    __slots__ = ("message_type", "reason")

    def __init__(self, message_type: str, reason: str | None = None):
        self.message_type = message_type  # broker_report / news / general / ambiguous
        self.reason = reason


async def classify_message(parsed: ParsedReport) -> S2aResult:
    """
    S2a: 메시지 유형 분류.

    Returns S2aResult with message_type:
      - broker_report → S2b로 진행
      - news / general → skip
      - ambiguous → pending_messages에 저장 후 skip
    """
    if not settings.llm_enabled or not settings.anthropic_api_key:
        return S2aResult("broker_report")  # LLM 비활성 시 통과

    try:
        result, response = await _call_s2a(parsed.raw_text)
    except Exception as e:
        log.warning("s2a_failed", error=str(e), title=parsed.title[:50])
        return S2aResult("broker_report")  # 실패 시 통과 (누락 방지)

    usage = response.usage
    message_type = (result or {}).get("message_type", "general")
    reason = (result or {}).get("reason")

    await record_llm_usage(
        model=settings.llm_model,
        purpose="s2a_classify",
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        source_channel=parsed.source_channel,
        message_type=message_type,
    )

    log.debug("s2a_classified", type=message_type, channel=parsed.source_channel)
    return S2aResult(message_type, reason)


async def extract_metadata(
    parsed: ParsedReport,
    pdf_meta_context: str | None = None,
    report_id: int | None = None,
) -> ParsedReport:
    """
    S2b: broker_report 확정 후 메타데이터 정밀 추출.
    pdf_meta_context: PDF 메타데이터 문자열 (있으면 컨텍스트로 제공)

    Returns: 보강된 ParsedReport (실패 시 원본 그대로)
    """
    if not settings.llm_enabled or not settings.anthropic_api_key:
        return parsed

    try:
        result, response = await _call_s2b(parsed.raw_text, pdf_meta_context)
    except Exception as e:
        log.warning("s2b_failed", error=str(e), title=parsed.title[:50])
        return parsed  # fallback: 정규식 결과 그대로

    usage = response.usage
    await record_llm_usage(
        model=settings.llm_model,
        purpose="s2b_extract",
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        source_channel=parsed.source_channel,
        report_id=report_id,
        message_type="broker_report",
    )

    if result is None:
        log.warning("s2b_no_result", title=parsed.title[:50])
        return parsed

    def _merge(parsed_val, llm_val, normalizer=None):
        if llm_val:
            return normalizer(llm_val) if normalizer else llm_val
        return parsed_val

    parsed.broker     = _merge(parsed.broker,     result.get("broker"),     normalize_broker)
    parsed.stock_name = _merge(parsed.stock_name, result.get("stock_name"))
    parsed.stock_code = _merge(parsed.stock_code, result.get("stock_code"))
    parsed.title      = _merge(parsed.title,      result.get("title"))
    parsed.analyst    = _merge(parsed.analyst,    result.get("analyst"))
    parsed.opinion    = _merge(parsed.opinion,    result.get("opinion"),    normalize_opinion)
    parsed.sector     = _merge(parsed.sector,     result.get("sector"))
    parsed.report_type = _merge(parsed.report_type, result.get("report_type"))

    if tp := result.get("target_price"):
        parsed.target_price = parse_price(tp) or parsed.target_price
    if prev_tp := result.get("prev_target_price"):
        parsed.prev_target_price = parse_price(prev_tp) or parsed.prev_target_price
    parsed.prev_opinion = _merge(parsed.prev_opinion, result.get("prev_opinion"), normalize_opinion)

    log.info("s2b_extracted", broker=parsed.broker, title=(parsed.title or "")[:50])
    return parsed
