"""ReportChartText 저장소 — chart_digitize 결과 캐시 저장/조회.

fail-silent 패턴: 저장/조회 실패 시 예외 전파 없이 log.warning만.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

import structlog
from sqlalchemy.dialects.postgresql import insert

from config.settings import settings
from db.models import ReportChartText
from db.session import AsyncSessionLocal

if TYPE_CHECKING:
    from parser.chart_digitizer import DigitizeResult

log = structlog.get_logger(__name__)


async def load_chart_text(report_id: int) -> "DigitizeResult | None":
    """Cache hit 시 DigitizeResult 반환, 미스 시 None.

    예외 발생 시 None 반환 + log.warning (fail-silent).
    """
    from parser.chart_digitizer import DigitizeResult

    try:
        from sqlalchemy import select
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(ReportChartText).where(ReportChartText.report_id == report_id)
            )
            row = result.scalar_one_or_none()

        if row is None:
            return None

        return DigitizeResult(
            texts=list(row.chart_texts or []),
            total_input_tokens=row.total_input_tokens or 0,
            total_output_tokens=row.total_output_tokens or 0,
            total_cost_usd=Decimal(str(row.total_cost_usd or "0")),
            image_count=row.image_count,
            success_count=row.success_count,
        )
    except Exception as e:
        log.warning("chart_text_load_failed", report_id=report_id, error=str(e))
        return None


async def save_chart_text(report_id: int, result: "DigitizeResult") -> None:
    """ON CONFLICT DO UPDATE upsert. 자체 세션. fail-silent."""
    now = datetime.now(timezone.utc)
    try:
        async with AsyncSessionLocal() as session:
            stmt = insert(ReportChartText).values(
                report_id=report_id,
                chart_texts=result.texts,
                image_count=result.image_count,
                success_count=result.success_count,
                model=settings.gemini_model,
                total_input_tokens=result.total_input_tokens,
                total_output_tokens=result.total_output_tokens,
                total_cost_usd=result.total_cost_usd,
                created_at=now,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["report_id"],
                set_={
                    "chart_texts": stmt.excluded.chart_texts,
                    "image_count": stmt.excluded.image_count,
                    "success_count": stmt.excluded.success_count,
                    "model": stmt.excluded.model,
                    "total_input_tokens": stmt.excluded.total_input_tokens,
                    "total_output_tokens": stmt.excluded.total_output_tokens,
                    "total_cost_usd": stmt.excluded.total_cost_usd,
                    "updated_at": now,
                },
            )
            await session.execute(stmt)
            await session.commit()
        log.debug("chart_text_saved", report_id=report_id,
                  success_count=result.success_count,
                  image_count=result.image_count)
    except Exception as e:
        log.warning("chart_text_save_failed", report_id=report_id, error=str(e))
