"""Agent 챗봇 API 엔드포인트."""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from datetime import datetime

import structlog
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: F401 — used for type hints in Depends

from agent.chat_handler import get_default_provider, stream_to_sse
from agent.context_builder import build_context
from agent.prompt_templates import SYSTEM_PROMPT, build_user_prompt
from api.deps import get_db
from api.schemas import (
    ChatMessageListResponse,
    ChatMessageResponse,
    ChatRequest,
    ChatSessionListResponse,
    ChatSessionResponse,
)
from config.settings import settings
from db.models import ChatMessage, ChatSession
from db.session import AsyncSessionLocal

log = structlog.get_logger(__name__)

router = APIRouter(prefix="/agent", tags=["agent"])

# 슬라이딩 윈도우: LLM에 전달할 최대 히스토리 메시지 수
MAX_HISTORY_MESSAGES = 20

# LLM 응답 실패 시 저장할 placeholder 내용
_ERROR_PLACEHOLDER = "[오류] 응답 생성에 실패했습니다."


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------


def _session_to_response(session: ChatSession, message_count: int = 0) -> ChatSessionResponse:
    return ChatSessionResponse(
        id=session.id,
        title=session.title,
        user_id=session.user_id,
        message_count=message_count,
        created_at=session.created_at,
        updated_at=session.updated_at,
    )


def _message_to_response(msg: ChatMessage) -> ChatMessageResponse:
    return ChatMessageResponse(
        id=msg.id,
        session_id=msg.session_id,
        role=msg.role,
        content=msg.content,
        context_report_count=msg.context_report_count,
        created_at=msg.created_at,
    )


# ---------------------------------------------------------------------------
# POST /agent/chat
# ---------------------------------------------------------------------------


@router.post("/chat")
async def chat(
    body: ChatRequest,
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """SSE 스트리밍 채팅. session_id 없으면 새 세션을 생성한다."""

    # ── Fix #3: 모든 DB 작업을 StreamingResponse 반환 전에 완료 ──

    # 세션 조회 또는 생성
    if body.session_id is not None:
        result = await db.execute(
            select(ChatSession).where(ChatSession.id == body.session_id)
        )
        session = result.scalar_one_or_none()
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
    else:
        # 첫 메시지 앞 50자를 제목으로
        title = body.message[:50] if len(body.message) > 50 else body.message
        session = ChatSession(title=title)
        db.add(session)
        await db.flush()  # id 할당

    session_id: int = session.id

    # 대화 히스토리 로드
    history_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    )
    history: list[ChatMessage] = list(history_result.scalars().all())

    # 컨텍스트 빌드
    context_yaml: str | None = await build_context(body.message, db)
    context_report_count = 0
    if context_yaml:
        # yaml 블록 수 대략 산정 (- report_id: 등장 횟수)
        context_report_count = context_yaml.count("report_id:")

    # 유저 메시지 저장
    user_msg = ChatMessage(
        session_id=session_id,
        role="user",
        content=body.message,
        context_report_count=context_report_count,
    )
    db.add(user_msg)
    await db.commit()

    # ── Fix #2: 슬라이딩 윈도우 — 최근 MAX_HISTORY_MESSAGES 개만 유지 ──
    # messages 목록 구성 (히스토리 + 새 유저 메시지)
    history_dicts: list[dict] = [
        {"role": m.role, "content": m.content} for m in history
    ]
    history_dicts = history_dicts[-MAX_HISTORY_MESSAGES:]
    messages: list[dict] = history_dicts + [
        {"role": "user", "content": build_user_prompt(body.message, context_yaml)}
    ]

    provider = get_default_provider()
    model = settings.agent_model

    # ── sse_generator는 순수 데이터(messages, model, system)만 사용 ──
    async def sse_generator() -> AsyncGenerator[str, None]:
        """SSE 이벤트를 yield하며 완료 후 assistant 응답을 DB에 저장."""
        accumulated: list[str] = []

        async for event in stream_to_sse(
            provider=provider,
            messages=messages,
            model=model,
            system=SYSTEM_PROMPT,
        ):
            # 텍스트 청크면 누적
            if event.startswith("data: "):
                try:
                    payload = json.loads(event[6:])
                    if payload.get("type") == "text":
                        accumulated.append(payload.get("text", ""))
                except (json.JSONDecodeError, KeyError):
                    pass
            yield event

        # ── Fix #1: 오류 시에도 assistant placeholder 저장 ──
        # 응답 완료 후 assistant 메시지 저장 (별도 세션 사용 — Fix #3 유지)
        full_response = "".join(accumulated) if accumulated else _ERROR_PLACEHOLDER
        async with AsyncSessionLocal() as save_session:
            assistant_msg = ChatMessage(
                session_id=session_id,
                role="assistant",
                content=full_response,
            )
            save_session.add(assistant_msg)
            await save_session.commit()

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# GET /agent/sessions
# ---------------------------------------------------------------------------


@router.get("/sessions", response_model=ChatSessionListResponse)
async def list_sessions(
    db: AsyncSession = Depends(get_db),
) -> ChatSessionListResponse:
    """대화 세션 목록 (최신순). 메시지 수 포함."""
    result = await db.execute(
        select(
            ChatSession,
            func.count(ChatMessage.id).label("message_count"),
        )
        .outerjoin(ChatMessage, ChatMessage.session_id == ChatSession.id)
        .group_by(ChatSession.id)
        .order_by(ChatSession.created_at.desc())
    )
    rows = result.all()
    return ChatSessionListResponse(
        sessions=[
            _session_to_response(session, message_count=cnt)
            for session, cnt in rows
        ]
    )


# ---------------------------------------------------------------------------
# GET /agent/sessions/{id}/messages
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}/messages", response_model=ChatMessageListResponse)
async def get_session_messages(
    session_id: int,
    db: AsyncSession = Depends(get_db),
) -> ChatMessageListResponse:
    """특정 세션의 메시지 히스토리 (시간순)."""
    # 세션 존재 확인
    session_result = await db.execute(
        select(ChatSession).where(ChatSession.id == session_id)
    )
    if session_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Session not found")

    msgs_result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
    )
    msgs = list(msgs_result.scalars().all())
    return ChatMessageListResponse(
        messages=[_message_to_response(m) for m in msgs]
    )


# ---------------------------------------------------------------------------
# DELETE /agent/sessions/{id}
# ---------------------------------------------------------------------------


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: int,
    db: AsyncSession = Depends(get_db),
) -> None:
    """세션과 관련 메시지 모두 삭제."""
    session_result = await db.execute(
        select(ChatSession).where(ChatSession.id == session_id)
    )
    session = session_result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    await db.execute(
        delete(ChatMessage).where(ChatMessage.session_id == session_id)
    )
    await db.delete(session)
    await db.commit()
