"""Stage 2 LLM 파서 — Claude Haiku로 메시지 분류 + 메타데이터 정밀 추출."""
import structlog
from anthropic import AsyncAnthropic, RateLimitError, APIConnectionError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config.settings import settings
from parser.base import ParsedReport
from parser.normalizer import normalize_broker, normalize_opinion, parse_price

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


SYSTEM_PROMPT = """\
당신은 한국 증권사 리포트 텔레그램 메시지 분류 및 메타데이터 추출 전문가입니다.

메시지를 분석하여 다음 중 하나로 분류하세요:
- broker_report: 증권사가 발행한 종목/산업 리서치 리포트
- news: 뉴스 기사, 시황, 공시 등
- general: 광고, 안내, 잡담 등

broker_report인 경우 메타데이터를 정밀하게 추출하세요.
마크다운 링크([]()) 안의 텍스트, 볼드(**) 등 서식은 무시하고 실제 내용만 추출하세요.
"""

EXTRACT_TOOL = {
    "name": "classify_and_extract",
    "description": "텔레그램 메시지를 분류하고 증권사 리포트인 경우 메타데이터를 추출합니다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "message_type": {
                "type": "string",
                "enum": ["broker_report", "news", "general"],
                "description": "메시지 유형",
            },
            "broker": {
                "type": "string",
                "description": "증권사명 (예: 미래에셋증권, KB증권). broker_report일 때만.",
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
        "required": ["message_type"],
    },
}


@retry(
    stop=stop_after_attempt(settings.llm_max_retries + 1),
    wait=wait_exponential(multiplier=1, min=1, max=10),
    retry=retry_if_exception_type((RateLimitError, APIConnectionError)),
    reraise=True,
)
async def _call_llm(message_text: str) -> dict | None:
    """Claude Haiku API 호출 → tool_use 결과 dict 반환."""
    client = _get_client()
    response = await client.messages.create(
        model=settings.llm_model,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        tools=[EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "classify_and_extract"},
        messages=[{"role": "user", "content": message_text}],
    )

    for block in response.content:
        if block.type == "tool_use" and block.name == "classify_and_extract":
            return block.input
    return None


def _merge_field(parsed_val, llm_val, normalizer=None):
    """LLM 값이 있으면 무조건 덮어씀. normalizer가 있으면 적용."""
    if llm_val:
        return normalizer(llm_val) if normalizer else llm_val
    return parsed_val


async def enrich_with_llm(parsed: ParsedReport) -> ParsedReport | None:
    """
    LLM Stage 2 보강.

    Returns:
        ParsedReport — LLM으로 보강된 결과 (broker_report)
        None — 리포트가 아닌 메시지 (news/general) → 저장 skip
    """
    if not settings.llm_enabled:
        return parsed

    if not settings.anthropic_api_key:
        log.debug("llm_skipped_no_api_key")
        return parsed

    try:
        result = await _call_llm(parsed.raw_text)
    except Exception as e:
        log.warning("llm_call_failed", error=str(e), title=parsed.title[:50])
        return parsed  # fallback: 정규식 결과 그대로

    if result is None:
        log.warning("llm_no_tool_result", title=parsed.title[:50])
        return parsed

    message_type = result.get("message_type", "general")

    if message_type != "broker_report":
        log.debug("llm_filtered", message_type=message_type, title=parsed.title[:50])
        return None  # 리포트가 아님 → 저장 skip

    # LLM 값으로 정규식 결과 덮어쓰기
    parsed.broker = _merge_field(parsed.broker, result.get("broker"), normalize_broker)
    parsed.stock_name = _merge_field(parsed.stock_name, result.get("stock_name"))
    parsed.stock_code = _merge_field(parsed.stock_code, result.get("stock_code"))
    parsed.title = _merge_field(parsed.title, result.get("title"))
    parsed.analyst = _merge_field(parsed.analyst, result.get("analyst"))
    parsed.opinion = _merge_field(parsed.opinion, result.get("opinion"), normalize_opinion)
    parsed.sector = _merge_field(parsed.sector, result.get("sector"))
    parsed.report_type = _merge_field(parsed.report_type, result.get("report_type"))

    # 가격 필드는 parse_price 적용
    llm_tp = result.get("target_price")
    if llm_tp:
        parsed.target_price = parse_price(llm_tp) or parsed.target_price
    llm_prev_tp = result.get("prev_target_price")
    if llm_prev_tp:
        parsed.prev_target_price = parse_price(llm_prev_tp) or parsed.prev_target_price
    parsed.prev_opinion = _merge_field(
        parsed.prev_opinion, result.get("prev_opinion"), normalize_opinion
    )

    log.info("llm_enriched", broker=parsed.broker, title=parsed.title[:50])
    return parsed
