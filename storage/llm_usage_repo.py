"""llm_usage 테이블 기록 유틸리티 — 여러 LLM 모듈에서 공유."""
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import LlmUsage, calc_cost_usd
from db.session import AsyncSessionLocal

log = structlog.get_logger(__name__)


async def record_llm_usage(
    model: str,
    purpose: str,
    input_tokens: int,
    output_tokens: int,
    source_channel: str | None = None,
    report_id: int | None = None,
    message_type: str | None = None,
) -> None:
    """llm_usage 테이블에 API 호출 비용 기록. 실패해도 예외 전파 안 함."""
    try:
        cost = calc_cost_usd(model, input_tokens, output_tokens)
        async with AsyncSessionLocal() as session:
            session.add(LlmUsage(
                model=model,
                purpose=purpose,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                message_type=message_type,
                report_id=report_id,
                source_channel=source_channel,
            ))
            await session.commit()
        log.debug(
            "llm_usage_recorded",
            purpose=purpose,
            message_type=message_type,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=float(cost),
        )
    except Exception as e:
        log.warning("llm_usage_record_failed", error=str(e))
