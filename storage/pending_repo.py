"""pending_messages CRUD — S2a ambiguous 메시지 저장/조회/처리."""
from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import PendingMessage

log = structlog.get_logger(__name__)


async def save_pending(
    session: AsyncSession,
    source_channel: str,
    source_message_id: int | None,
    raw_text: str | None,
    pdf_url: str | None,
    s2a_label: str,
    s2a_reason: str | None = None,
) -> PendingMessage:
    """ambiguous 메시지를 pending_messages에 저장."""
    msg = PendingMessage(
        source_channel=source_channel,
        source_message_id=source_message_id,
        raw_text=raw_text,
        pdf_url=pdf_url,
        s2a_label=s2a_label,
        s2a_reason=s2a_reason,
        review_status="pending",
    )
    session.add(msg)
    await session.flush()
    log.info("pending_saved", id=msg.id, channel=source_channel, label=s2a_label)
    return msg


async def list_pending(
    session: AsyncSession,
    status: str = "pending",
    channel: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[PendingMessage], int]:
    """검토 대기 메시지 목록 + 총 건수."""
    q = select(PendingMessage).where(PendingMessage.review_status == status)
    if channel:
        q = q.where(PendingMessage.source_channel == channel)
    q = q.order_by(PendingMessage.created_at.desc())

    count_q = select(func.count()).select_from(
        q.subquery()
    )
    total = (await session.execute(count_q)).scalar_one()
    rows = (await session.execute(q.offset(offset).limit(limit))).scalars().all()
    return list(rows), total


async def resolve_pending(
    session: AsyncSession,
    pending_id: int,
    decision: str,  # "broker_report" | "discarded"
) -> PendingMessage | None:
    """검토 결과 반영. decision: broker_report(리포트로 처리) / discarded(버림)."""
    msg = await session.get(PendingMessage, pending_id)
    if msg is None:
        return None
    msg.review_status = decision
    msg.reviewed_at = datetime.now(timezone.utc)
    await session.flush()
    log.info("pending_resolved", id=pending_id, decision=decision)
    return msg


async def get_pending_stats(session: AsyncSession) -> dict:
    """상태별 건수 집계."""
    rows = (
        await session.execute(
            select(PendingMessage.review_status, func.count())
            .group_by(PendingMessage.review_status)
        )
    ).all()
    return {status: count for status, count in rows}
