"""LLM 파서 — S2a(분류)."""
from __future__ import annotations

import structlog
from anthropic import AsyncAnthropic, RateLimitError, APIConnectionError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config.settings import settings
from parser.base import ParsedReport
from parser.rate_limit import RateLimitGate
from storage.llm_usage_repo import record_llm_usage

log = structlog.get_logger(__name__)

_client: AsyncAnthropic | None = None

# Global backoff gate — when one S2a call hits a rate limit all callers pause
_s2a_gate = RateLimitGate("s2a_haiku")


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

분류 기준 (중요):
- PDF 파일명에 종목명, 날짜, 종목코드 등이 포함되어 있으면 증권사명이 없어도 broker_report
- PDF 링크 또는 증권사명 + 종목명 조합이 명확하면 broker_report
- 파일명 패턴 예시: "삼성전자_20210629_Hana_719274.pdf", "야스255440_20210621.pdf" → broker_report
- ambiguous는 텍스트가 심하게 손상되었거나 내용이 너무 짧아 전혀 판단 불가능한 경우에만 사용
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
    await _s2a_gate.wait()
    client = _get_client()
    try:
        response = await client.messages.create(
            model=settings.llm_model,
            max_tokens=128,
            system=_S2A_SYSTEM,
            tools=[_S2A_TOOL],
            tool_choice={"type": "tool", "name": "classify_message"},
            messages=[{"role": "user", "content": message_text}],
        )
    except RateLimitError as e:
        retry_after = 60.0
        try:
            retry_after = float(e.response.headers.get("retry-after", 60))
        except Exception:
            pass
        await _s2a_gate.trigger_backoff(retry_after)
        raise
    for block in response.content:
        if block.type == "tool_use" and block.name == "classify_message":
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
