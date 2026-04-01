"""Tests for api/routers/agent.py — Agent 챗봇 API 엔드포인트."""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from api.routers.agent import router
from api.deps import get_db
from api.schemas import ChatRequest, ChatSessionResponse, ChatMessageResponse
from db.models import ChatMessage, ChatSession


# ──────────────────────────────────────────────
# 헬퍼: SSE 파싱
# ──────────────────────────────────────────────

def parse_sse_events(raw: str) -> list[dict]:
    """SSE 스트림 문자열에서 data JSON 추출."""
    events = []
    for line in raw.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


# ──────────────────────────────────────────────
# 헬퍼: Mock DB 객체 생성
# ──────────────────────────────────────────────

def _make_session_mock(sid: int = 1, title: str = "테스트 세션") -> MagicMock:
    """ChatSession처럼 동작하는 MagicMock."""
    now = datetime.now(tz=timezone.utc)
    m = MagicMock(spec=ChatSession)
    m.id = sid
    m.title = title
    m.user_id = None
    m.created_at = now
    m.updated_at = now
    return m


def _make_message_mock(mid: int, session_id: int, role: str, content: str) -> MagicMock:
    """ChatMessage처럼 동작하는 MagicMock."""
    now = datetime.now(tz=timezone.utc)
    m = MagicMock(spec=ChatMessage)
    m.id = mid
    m.session_id = session_id
    m.role = role
    m.content = content
    m.context_report_count = None
    m.created_at = now
    return m


def _scalar_result(obj) -> MagicMock:
    """scalar_one_or_none() 반환 mock."""
    r = MagicMock()
    r.scalar_one_or_none.return_value = obj
    return r


def _scalars_result(objects: list) -> MagicMock:
    """scalars().all() 반환 mock."""
    r = MagicMock()
    r.scalars.return_value.all.return_value = objects
    return r


def _all_result(rows: list) -> MagicMock:
    """rows.all() 반환 mock — 다중 컬럼 쿼리."""
    r = MagicMock()
    r.all.return_value = rows
    return r


def _make_db(
    session_obj=None,
    messages: list | None = None,
    session_rows: list | None = None,
) -> AsyncMock:
    """AsyncSession mock."""
    messages = messages or []
    db = AsyncMock()

    call_count = 0

    async def _execute(stmt, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        stmt_str = str(stmt)

        # list_sessions: JOIN 쿼리 → (ChatSession, count) 행
        if session_rows is not None:
            return _all_result(session_rows)

        # ChatSession SELECT (단일)
        if "chat_sessions" in stmt_str and "DELETE" not in stmt_str.upper():
            return _scalar_result(session_obj)

        # ChatMessage SELECT/DELETE
        if "chat_messages" in stmt_str:
            return _scalars_result(messages)

        return _scalars_result([])

    db.execute = _execute
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.delete = AsyncMock()
    db.add = MagicMock()

    return db


def _make_app(db: AsyncMock) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    return app


# ──────────────────────────────────────────────
# Mock 패치 헬퍼
# ──────────────────────────────────────────────

async def _fake_sse(messages, model, system, tools, db_session, max_iterations=10):
    yield 'data: {"type": "text", "text": "안녕"}\n\n'
    yield 'data: {"type": "text", "text": "하세요"}\n\n'
    yield 'data: {"type": "done"}\n\n'


def _stream_patch():
    return patch("api.routers.agent.stream_agent_response", side_effect=_fake_sse)


def _session_local_patch():
    """AsyncSessionLocal() context manager mock.

    agent.py calls AsyncSessionLocal() twice:
      1. Inside sse_generator as `tool_session` (passed to stream_agent_response)
      2. After streaming as `save_session` (to persist the assistant message)

    We return two distinct AsyncMock sessions so tests can assert on save_session.
    """
    tool_db = AsyncMock()
    tool_db.add = MagicMock()
    tool_db.commit = AsyncMock()

    save_db = AsyncMock()
    save_db.add = MagicMock()
    save_db.commit = AsyncMock()

    sessions = [tool_db, save_db]
    call_index = 0

    @asynccontextmanager
    async def _fake_session_local():
        nonlocal call_index
        db = sessions[call_index % len(sessions)]
        call_index += 1
        yield db

    return patch("api.routers.agent.AsyncSessionLocal", side_effect=_fake_session_local), save_db


# ──────────────────────────────────────────────
# POST /api/agent/chat
# ──────────────────────────────────────────────

class TestChatEndpoint:
    @pytest.mark.asyncio
    async def test_chat_new_session_returns_200(self):
        """session_id 없으면 200 반환."""
        db = _make_db()
        added = []

        def _add(obj):
            if isinstance(obj, ChatSession):
                obj.id = 1
            added.append(obj)

        db.add = _add

        sl_patch, _ = _session_local_patch()
        app = _make_app(db)
        with _stream_patch(), sl_patch:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/api/agent/chat", json={"message": "삼성전자"})
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_chat_response_media_type(self):
        """media_type=text/event-stream."""
        db = _make_db()

        def _add(obj):
            if isinstance(obj, ChatSession):
                obj.id = 1

        db.add = _add

        sl_patch, _ = _session_local_patch()
        app = _make_app(db)
        with _stream_patch(), sl_patch:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/api/agent/chat", json={"message": "안녕"})
        assert "text/event-stream" in resp.headers["content-type"]

    @pytest.mark.asyncio
    async def test_chat_sse_events_structure(self):
        """SSE 이벤트: text 청크 + done."""
        db = _make_db()

        def _add(obj):
            if isinstance(obj, ChatSession):
                obj.id = 1

        db.add = _add

        sl_patch, _ = _session_local_patch()
        app = _make_app(db)
        with _stream_patch(), sl_patch:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/api/agent/chat", json={"message": "안녕"})

        events = parse_sse_events(resp.text)
        types = [e["type"] for e in events]
        assert "text" in types
        assert "done" in types
        assert types[-1] == "done"

    @pytest.mark.asyncio
    async def test_chat_sse_text_chunks_accumulated(self):
        """두 텍스트 청크가 모두 전달된다."""
        db = _make_db()

        def _add(obj):
            if isinstance(obj, ChatSession):
                obj.id = 1

        db.add = _add

        sl_patch, _ = _session_local_patch()
        app = _make_app(db)
        with _stream_patch(), sl_patch:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post("/api/agent/chat", json={"message": "안녕"})

        events = parse_sse_events(resp.text)
        text_events = [e for e in events if e["type"] == "text"]
        assert len(text_events) == 2
        combined = "".join(e["text"] for e in text_events)
        assert combined == "안녕하세요"

    @pytest.mark.asyncio
    async def test_chat_saves_user_message(self):
        """유저 메시지가 ChatMessage로 db.add 호출."""
        db = _make_db()
        added = []

        def _add(obj):
            if isinstance(obj, ChatSession):
                obj.id = 1
            added.append(obj)

        db.add = _add

        sl_patch, _ = _session_local_patch()
        app = _make_app(db)
        with _stream_patch(), sl_patch:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                await ac.post("/api/agent/chat", json={"message": "테스트 질문"})

        user_msgs = [o for o in added if isinstance(o, ChatMessage) and o.role == "user"]
        assert len(user_msgs) == 1
        assert user_msgs[0].content == "테스트 질문"

    @pytest.mark.asyncio
    async def test_chat_creates_new_session_obj(self):
        """session_id 없으면 ChatSession이 추가된다."""
        db = _make_db()
        added = []

        def _add(obj):
            if isinstance(obj, ChatSession):
                obj.id = 1
            added.append(obj)

        db.add = _add

        sl_patch, _ = _session_local_patch()
        app = _make_app(db)
        with _stream_patch(), sl_patch:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                await ac.post("/api/agent/chat", json={"message": "새 세션"})

        session_objs = [o for o in added if isinstance(o, ChatSession)]
        assert len(session_objs) == 1

    @pytest.mark.asyncio
    async def test_chat_title_truncated_at_50(self):
        """50자 초과 메시지 → 세션 제목 50자로 잘림."""
        db = _make_db()
        created_sessions = []

        def _add(obj):
            if isinstance(obj, ChatSession):
                obj.id = 1
                created_sessions.append(obj)

        db.add = _add

        long_msg = "가" * 60
        sl_patch, _ = _session_local_patch()
        app = _make_app(db)
        with _stream_patch(), sl_patch:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                await ac.post("/api/agent/chat", json={"message": long_msg})

        assert len(created_sessions) == 1
        assert len(created_sessions[0].title) == 50

    @pytest.mark.asyncio
    async def test_chat_with_existing_session_no_new_session(self):
        """session_id 제공 시 새 ChatSession 생성 안 함."""
        existing = _make_session_mock(42, "기존 세션")
        db = _make_db(session_obj=existing)
        added_sessions = []

        def _add(obj):
            if isinstance(obj, ChatSession):
                added_sessions.append(obj)

        db.add = _add

        sl_patch, _ = _session_local_patch()
        app = _make_app(db)
        with _stream_patch(), sl_patch:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                resp = await ac.post(
                    "/api/agent/chat",
                    json={"message": "후속 질문", "session_id": 42},
                )
        assert resp.status_code == 200
        assert len(added_sessions) == 0

    @pytest.mark.asyncio
    async def test_chat_invalid_session_id_returns_404(self):
        """존재하지 않는 session_id → 404."""
        db = _make_db(session_obj=None)

        app = _make_app(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/agent/chat",
                json={"message": "질문", "session_id": 99999},
            )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_chat_includes_history_in_messages(self):
        """기존 히스토리가 LLM messages에 포함된다."""
        existing = _make_session_mock(10, "히스토리 세션")
        prev_user = _make_message_mock(1, 10, "user", "이전 질문")
        prev_asst = _make_message_mock(2, 10, "assistant", "이전 답변")
        db = _make_db(session_obj=existing, messages=[prev_user, prev_asst])

        def _add(obj):
            pass

        db.add = _add

        captured: list[list[dict]] = []

        async def _capturing_sse(messages, model, system, tools, db_session, max_iterations=10):
            captured.append(messages)
            yield 'data: {"type": "text", "text": "응답"}\n\n'
            yield 'data: {"type": "done"}\n\n'

        sl_patch, _ = _session_local_patch()
        app = _make_app(db)
        with patch("api.routers.agent.stream_agent_response", side_effect=_capturing_sse), sl_patch:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                await ac.post(
                    "/api/agent/chat",
                    json={"message": "새 질문", "session_id": 10},
                )

        assert len(captured) == 1
        msgs = captured[0]
        # 히스토리 2개 + 새 유저 메시지 1개
        assert len(msgs) == 3
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "이전 질문"
        assert msgs[1]["role"] == "assistant"
        assert msgs[2]["role"] == "user"
        assert msgs[2]["content"] == "새 질문"

    @pytest.mark.asyncio
    async def test_chat_context_report_count_stored(self):
        """context_report_count가 user 메시지에 저장된다."""
        db = _make_db()
        added_messages = []

        def _add(obj):
            if isinstance(obj, ChatSession):
                obj.id = 1
            if isinstance(obj, ChatMessage):
                added_messages.append(obj)

        db.add = _add

        sl_patch, _ = _session_local_patch()
        app = _make_app(db)
        with _stream_patch(), sl_patch:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                await ac.post("/api/agent/chat", json={"message": "삼성전자"})

        user_msgs = [m for m in added_messages if m.role == "user"]
        assert len(user_msgs) == 1
        # tool-use 모드에서는 context pre-fetch 없이 항상 0
        assert user_msgs[0].context_report_count == 0

    @pytest.mark.asyncio
    async def test_chat_system_prompt_passed(self):
        """TOOL_SYSTEM_PROMPT가 stream_agent_response에 전달된다."""
        db = _make_db()

        def _add(obj):
            if isinstance(obj, ChatSession):
                obj.id = 1

        db.add = _add

        captured_kwargs = []

        async def _capturing_sse(messages, model, system, tools, db_session, max_iterations=10):
            captured_kwargs.append({"system": system, "model": model})
            yield 'data: {"type": "text", "text": "ok"}\n\n'
            yield 'data: {"type": "done"}\n\n'

        sl_patch, _ = _session_local_patch()
        app = _make_app(db)
        with patch("api.routers.agent.stream_agent_response", side_effect=_capturing_sse), sl_patch:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                await ac.post("/api/agent/chat", json={"message": "질문"})

        assert len(captured_kwargs) == 1
        assert captured_kwargs[0]["system"] is not None
        assert len(captured_kwargs[0]["system"]) > 0

    @pytest.mark.asyncio
    async def test_chat_error_saves_placeholder_assistant_message(self):
        """Fix #1: LLM 에러(텍스트 누적 없음)이면 placeholder assistant 메시지를 저장."""
        db = _make_db()

        def _add(obj):
            if isinstance(obj, ChatSession):
                obj.id = 1

        db.add = _add

        async def _error_sse(messages, model, system, tools, db_session, max_iterations=10):
            yield 'data: {"type": "error", "message": "LLM timeout"}\n\n'

        sl_patch, save_db = _session_local_patch()
        app = _make_app(db)
        with patch("api.routers.agent.stream_agent_response", side_effect=_error_sse), sl_patch:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                await ac.post("/api/agent/chat", json={"message": "질문"})

        save_db.add.assert_called_once()
        saved_obj = save_db.add.call_args[0][0]
        assert isinstance(saved_obj, ChatMessage)
        assert saved_obj.role == "assistant"
        assert "[오류]" in saved_obj.content

    @pytest.mark.asyncio
    async def test_chat_history_truncated_to_max(self):
        """Fix #2: 히스토리가 MAX_HISTORY_MESSAGES 개로 잘린다."""
        from api.routers.agent import MAX_HISTORY_MESSAGES

        existing = _make_session_mock(10, "긴 히스토리 세션")
        # MAX_HISTORY_MESSAGES + 10 개의 메시지 생성 (user/assistant 번갈아)
        many_messages = []
        for i in range(MAX_HISTORY_MESSAGES + 10):
            role = "user" if i % 2 == 0 else "assistant"
            many_messages.append(_make_message_mock(i + 1, 10, role, f"메시지 {i}"))

        db = _make_db(session_obj=existing, messages=many_messages)

        def _add(obj):
            pass

        db.add = _add

        captured: list[list[dict]] = []

        async def _capturing_sse(messages, model, system, tools, db_session, max_iterations=10):
            captured.append(messages)
            yield 'data: {"type": "text", "text": "응답"}\n\n'
            yield 'data: {"type": "done"}\n\n'

        sl_patch, _ = _session_local_patch()
        app = _make_app(db)
        with patch("api.routers.agent.stream_agent_response", side_effect=_capturing_sse), sl_patch:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                await ac.post(
                    "/api/agent/chat",
                    json={"message": "새 질문", "session_id": 10},
                )

        assert len(captured) == 1
        msgs = captured[0]
        # 히스토리 최대 MAX_HISTORY_MESSAGES + 새 유저 메시지 1개
        assert len(msgs) == MAX_HISTORY_MESSAGES + 1

    @pytest.mark.asyncio
    async def test_chat_history_within_limit_not_truncated(self):
        """Fix #2: 히스토리가 MAX_HISTORY_MESSAGES 이하면 전부 포함된다."""
        from api.routers.agent import MAX_HISTORY_MESSAGES

        existing = _make_session_mock(10, "짧은 히스토리 세션")
        short_history = [
            _make_message_mock(1, 10, "user", "이전 질문"),
            _make_message_mock(2, 10, "assistant", "이전 답변"),
        ]
        assert len(short_history) <= MAX_HISTORY_MESSAGES

        db = _make_db(session_obj=existing, messages=short_history)

        def _add(obj):
            pass

        db.add = _add

        captured: list[list[dict]] = []

        async def _capturing_sse(messages, model, system, tools, db_session, max_iterations=10):
            captured.append(messages)
            yield 'data: {"type": "text", "text": "응답"}\n\n'
            yield 'data: {"type": "done"}\n\n'

        sl_patch, _ = _session_local_patch()
        app = _make_app(db)
        with patch("api.routers.agent.stream_agent_response", side_effect=_capturing_sse), sl_patch:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                await ac.post(
                    "/api/agent/chat",
                    json={"message": "새 질문", "session_id": 10},
                )

        msgs = captured[0]
        # 히스토리 2개 + 새 유저 메시지 1개
        assert len(msgs) == 3

    @pytest.mark.asyncio
    async def test_chat_assistant_saved_after_stream(self):
        """스트리밍 완료 후 assistant 메시지가 save_session에 저장된다."""
        db = _make_db()

        def _add(obj):
            if isinstance(obj, ChatSession):
                obj.id = 1

        db.add = _add

        sl_patch, save_db = _session_local_patch()
        app = _make_app(db)
        with _stream_patch(), sl_patch:
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                await ac.post("/api/agent/chat", json={"message": "질문"})

        # save_db.add가 호출되었는지 확인
        save_db.add.assert_called_once()
        saved_obj = save_db.add.call_args[0][0]
        assert isinstance(saved_obj, ChatMessage)
        assert saved_obj.role == "assistant"
        assert saved_obj.content == "안녕하세요"


# ──────────────────────────────────────────────
# GET /api/agent/sessions
# ──────────────────────────────────────────────

class TestListSessions:
    @pytest.mark.asyncio
    async def test_list_sessions_empty(self):
        """세션 없으면 빈 배열."""
        db = AsyncMock()
        db.execute = AsyncMock(return_value=_all_result([]))

        app = _make_app(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/agent/sessions")
        assert resp.status_code == 200
        assert resp.json() == {"sessions": []}

    @pytest.mark.asyncio
    async def test_list_sessions_returns_newest_first(self):
        """최신순 정렬."""
        s1 = _make_session_mock(1, "첫 번째")
        s2 = _make_session_mock(2, "두 번째")
        # DB 쿼리가 이미 최신순으로 정렬된 결과 반환
        rows = [(s2, 0), (s1, 0)]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_all_result(rows))

        app = _make_app(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/agent/sessions")

        data = resp.json()["sessions"]
        assert len(data) == 2
        assert data[0]["id"] == 2
        assert data[1]["id"] == 1

    @pytest.mark.asyncio
    async def test_list_sessions_includes_message_count(self):
        """message_count 필드 포함."""
        session = _make_session_mock(1, "메시지 있는 세션")
        rows = [(session, 5)]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_all_result(rows))

        app = _make_app(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/agent/sessions")

        data = resp.json()["sessions"]
        assert len(data) == 1
        assert data[0]["message_count"] == 5

    @pytest.mark.asyncio
    async def test_list_sessions_schema_fields(self):
        """응답 스키마 필드 확인."""
        session = _make_session_mock(1, "스키마 테스트")
        rows = [(session, 0)]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_all_result(rows))

        app = _make_app(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/agent/sessions")

        data = resp.json()["sessions"][0]
        for field in ("id", "title", "message_count", "created_at", "updated_at"):
            assert field in data, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_list_sessions_title_included(self):
        """제목이 응답에 포함된다."""
        session = _make_session_mock(1, "삼성전자 질문")
        rows = [(session, 2)]

        db = AsyncMock()
        db.execute = AsyncMock(return_value=_all_result(rows))

        app = _make_app(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/agent/sessions")

        assert resp.json()["sessions"][0]["title"] == "삼성전자 질문"


# ──────────────────────────────────────────────
# GET /api/agent/sessions/{id}/messages
# ──────────────────────────────────────────────

class TestGetSessionMessages:
    @pytest.mark.asyncio
    async def test_get_messages_empty(self):
        """메시지 없는 세션 → 빈 배열."""
        session = _make_session_mock(1, "빈 세션")
        db = _make_db(session_obj=session, messages=[])

        app = _make_app(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/agent/sessions/1/messages")
        assert resp.status_code == 200
        assert resp.json() == {"messages": []}

    @pytest.mark.asyncio
    async def test_get_messages_returns_in_order(self):
        """시간순 정렬 반환."""
        session = _make_session_mock(1, "순서 테스트")
        m1 = _make_message_mock(1, 1, "user", "첫 번째")
        m2 = _make_message_mock(2, 1, "assistant", "두 번째")
        db = _make_db(session_obj=session, messages=[m1, m2])

        app = _make_app(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/agent/sessions/1/messages")

        data = resp.json()["messages"]
        assert len(data) == 2
        assert data[0]["role"] == "user"
        assert data[1]["role"] == "assistant"

    @pytest.mark.asyncio
    async def test_get_messages_schema_fields(self):
        """메시지 응답 스키마 필드."""
        session = _make_session_mock(1, "스키마")
        msg = _make_message_mock(10, 1, "user", "질문")
        db = _make_db(session_obj=session, messages=[msg])

        app = _make_app(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/agent/sessions/1/messages")

        item = resp.json()["messages"][0]
        for field in ("id", "session_id", "role", "content", "created_at"):
            assert field in item, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_get_messages_session_not_found(self):
        """존재하지 않는 세션 → 404."""
        db = _make_db(session_obj=None, messages=[])

        app = _make_app(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/agent/sessions/99999/messages")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_messages_content_preserved(self):
        """메시지 내용이 정확히 반환된다."""
        session = _make_session_mock(1, "내용 테스트")
        msg = _make_message_mock(5, 1, "user", "삼성전자 분석 요청")
        db = _make_db(session_obj=session, messages=[msg])

        app = _make_app(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/api/agent/sessions/1/messages")

        assert resp.json()["messages"][0]["content"] == "삼성전자 분석 요청"


# ──────────────────────────────────────────────
# DELETE /api/agent/sessions/{id}
# ──────────────────────────────────────────────

class TestDeleteSession:
    @pytest.mark.asyncio
    async def test_delete_session_returns_204(self):
        """성공 시 204 반환."""
        session = _make_session_mock(1, "삭제할 세션")
        db = _make_db(session_obj=session, messages=[])

        app = _make_app(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.delete("/api/agent/sessions/1")
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_session_calls_db_delete(self):
        """db.delete가 세션 객체로 호출된다."""
        session = _make_session_mock(1, "삭제 대상")
        db = _make_db(session_obj=session, messages=[])

        app = _make_app(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            await ac.delete("/api/agent/sessions/1")

        db.delete.assert_called_once_with(session)

    @pytest.mark.asyncio
    async def test_delete_session_calls_execute_for_messages(self):
        """메시지 삭제를 위해 execute가 호출된다 (최소 2번)."""
        session = _make_session_mock(1, "삭제 대상")
        db = _make_db(session_obj=session, messages=[])

        execute_calls = []
        original_execute = db.execute

        async def _tracking_execute(stmt, *args, **kwargs):
            execute_calls.append(str(stmt))
            return await original_execute(stmt, *args, **kwargs)

        db.execute = _tracking_execute

        app = _make_app(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            await ac.delete("/api/agent/sessions/1")

        # SELECT session + DELETE messages = 최소 2번
        assert len(execute_calls) >= 2

    @pytest.mark.asyncio
    async def test_delete_session_not_found(self):
        """존재하지 않는 세션 삭제 → 404."""
        db = _make_db(session_obj=None, messages=[])

        app = _make_app(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.delete("/api/agent/sessions/99999")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_session_commits(self):
        """삭제 후 db.commit이 호출된다."""
        session = _make_session_mock(1, "커밋 테스트")
        db = _make_db(session_obj=session, messages=[])

        app = _make_app(db)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            await ac.delete("/api/agent/sessions/1")

        db.commit.assert_called()


# ──────────────────────────────────────────────
# 스키마 단위 테스트
# ──────────────────────────────────────────────

class TestSchemas:
    def test_chat_request_required_message(self):
        req = ChatRequest(message="안녕")
        assert req.message == "안녕"
        assert req.session_id is None

    def test_chat_request_with_session_id(self):
        req = ChatRequest(message="질문", session_id=42)
        assert req.session_id == 42

    def test_chat_request_no_session_id_defaults_none(self):
        req = ChatRequest(message="질문")
        assert req.session_id is None

    def test_chat_session_response_schema(self):
        now = datetime.now(tz=timezone.utc)
        resp = ChatSessionResponse(
            id=1,
            title="테스트",
            user_id=None,
            message_count=5,
            created_at=now,
            updated_at=now,
        )
        assert resp.id == 1
        assert resp.message_count == 5
        assert resp.title == "테스트"
        assert resp.user_id is None

    def test_chat_message_response_schema(self):
        now = datetime.now(tz=timezone.utc)
        resp = ChatMessageResponse(
            id=10,
            session_id=1,
            role="user",
            content="질문 내용",
            context_report_count=3,
            created_at=now,
        )
        assert resp.role == "user"
        assert resp.context_report_count == 3
        assert resp.content == "질문 내용"

    def test_chat_message_response_optional_context_count(self):
        now = datetime.now(tz=timezone.utc)
        resp = ChatMessageResponse(
            id=1,
            session_id=1,
            role="assistant",
            content="답변",
            context_report_count=None,
            created_at=now,
        )
        assert resp.context_report_count is None

    def test_chat_session_response_null_title(self):
        now = datetime.now(tz=timezone.utc)
        resp = ChatSessionResponse(
            id=1,
            title=None,
            user_id=None,
            message_count=0,
            created_at=now,
            updated_at=now,
        )
        assert resp.title is None

    def test_chat_session_response_null_updated_at(self):
        """Fix #4: updated_at은 Optional — None 허용."""
        now = datetime.now(tz=timezone.utc)
        resp = ChatSessionResponse(
            id=1,
            title="테스트",
            user_id=None,
            message_count=0,
            created_at=now,
            updated_at=None,
        )
        assert resp.updated_at is None
