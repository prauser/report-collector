"""pending_messages API — 검토 대기 메시지 조회/처리."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_session as get_db
from storage.pending_repo import get_pending_stats, list_pending, resolve_pending

router = APIRouter(tags=["pending"])


class PendingMessageOut(BaseModel):
    id: int
    source_channel: str
    source_message_id: int | None
    raw_text: str | None
    pdf_url: str | None
    s2a_label: str | None
    s2a_reason: str | None
    review_status: str
    created_at: str

    model_config = {"from_attributes": True}


class PendingListResponse(BaseModel):
    items: list[PendingMessageOut]
    total: int
    limit: int
    offset: int


class ResolveRequest(BaseModel):
    decision: str  # "broker_report" | "discarded"


@router.get("/pending", response_model=PendingListResponse)
async def list_pending_messages(
    status: str = "pending",
    channel: str | None = None,
    limit: int = 50,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    """검토 대기 메시지 목록."""
    items, total = await list_pending(db, status=status, channel=channel, limit=limit, offset=offset)
    return PendingListResponse(
        items=[
            PendingMessageOut(
                id=m.id,
                source_channel=m.source_channel,
                source_message_id=m.source_message_id,
                raw_text=m.raw_text,
                pdf_url=m.pdf_url,
                s2a_label=m.s2a_label,
                s2a_reason=m.s2a_reason,
                review_status=m.review_status,
                created_at=m.created_at.isoformat(),
            )
            for m in items
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/pending/{pending_id}/resolve")
async def resolve_pending_message(
    pending_id: int,
    body: ResolveRequest,
    db: AsyncSession = Depends(get_db),
):
    """검토 결과 반영. decision: broker_report | discarded"""
    if body.decision not in ("broker_report", "discarded"):
        raise HTTPException(status_code=400, detail="decision must be 'broker_report' or 'discarded'")

    msg = await resolve_pending(db, pending_id, body.decision)
    if msg is None:
        raise HTTPException(status_code=404, detail="pending message not found")

    await db.commit()
    return {"id": msg.id, "review_status": msg.review_status}


@router.get("/pending/stats")
async def pending_stats(db: AsyncSession = Depends(get_db)):
    """상태별 건수."""
    return await get_pending_stats(db)
