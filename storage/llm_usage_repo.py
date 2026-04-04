"""llm_usage 테이블 기록 유틸리티 — 여러 LLM 모듈에서 공유."""
import asyncio

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import LlmUsage, calc_cost_usd
from db.session import AsyncSessionLocal

log = structlog.get_logger(__name__)

_DB_RETRIES = 3
_DB_RETRY_DELAY = 2  # seconds


async def record_llm_usage(
    model: str,
    purpose: str,
    input_tokens: int,
    output_tokens: int,
    source_channel: str | None = None,
    report_id: int | None = None,
    message_type: str | None = None,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    is_batch: bool = False,
) -> None:
    """llm_usage 테이블에 API 호출 비용 기록. 실패해도 예외 전파 안 함."""
    cost = calc_cost_usd(
        model, input_tokens, output_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_read_tokens=cache_read_tokens,
        is_batch=is_batch,
    )
    for attempt in range(1, _DB_RETRIES + 1):
        try:
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
            return
        except Exception as e:
            if attempt < _DB_RETRIES:
                await asyncio.sleep(_DB_RETRY_DELAY)
            else:
                log.warning("llm_usage_record_failed", error=str(e), attempts=attempt)
