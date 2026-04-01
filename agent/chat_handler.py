"""채팅 핸들러 — LLM 스트리밍 호출 + SSE 변환 + usage 기록."""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import AsyncIterator, Protocol, runtime_checkable

import structlog
from anthropic import AsyncAnthropic
from sqlalchemy.ext.asyncio import AsyncSession

from agent.tools import execute_tool
from config.settings import settings
from storage.llm_usage_repo import record_llm_usage

log = structlog.get_logger(__name__)

# ──────────────────────────────────────────────
# Module-level singleton agent client
# ──────────────────────────────────────────────

_agent_client: AsyncAnthropic | None = None


def _get_agent_client() -> AsyncAnthropic:
    """Module-level singleton AsyncAnthropic client for the agentic loop."""
    global _agent_client
    if _agent_client is None:
        _agent_client = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            timeout=settings.agent_timeout,
        )
    return _agent_client


# ──────────────────────────────────────────────
# LLM Provider 추상 인터페이스
# ──────────────────────────────────────────────

@runtime_checkable
class LLMChatProvider(Protocol):
    """LLM 채팅 프로바이더 프로토콜.

    다른 LLM provider(OpenAI, Gemini 등) 추가 시 이 Protocol을 구현하면 됩니다.
    """

    async def stream_chat(
        self,
        messages: list[dict],
        model: str,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]:
        """스트리밍 채팅 응답 생성.

        Args:
            messages: [{"role": "user"|"assistant", "content": str}, ...]
            model: 모델 ID
            system: 시스템 프롬프트 (선택)
            max_tokens: 최대 출력 토큰

        Yields:
            텍스트 청크 (str)

        Note:
            구현체는 완료 후 usage 기록 책임 있음 (record_llm_usage 호출).
        """
        ...


# ──────────────────────────────────────────────
# Anthropic 구현
# ──────────────────────────────────────────────

class AnthropicChatProvider:
    """Anthropic Claude 스트리밍 채팅 구현체."""

    def __init__(self, api_key: str | None = None, timeout: int = 120) -> None:
        self._client = AsyncAnthropic(
            api_key=api_key or settings.anthropic_api_key,
            timeout=timeout,
        )

    async def stream_chat(
        self,
        messages: list[dict],
        model: str,
        system: str | None = None,
        max_tokens: int = 4096,
    ) -> AsyncGenerator[str, None]:
        """Anthropic client.messages.stream() 기반 스트리밍."""
        kwargs: dict = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            kwargs["system"] = system

        input_tokens = 0
        output_tokens = 0

        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text

            # 스트림 완료 후 usage 가져오기
            final_msg = await stream.get_final_message()
            usage = final_msg.usage
            input_tokens = usage.input_tokens
            output_tokens = usage.output_tokens

        # usage 기록 (실패해도 예외 전파 안 함)
        await record_llm_usage(
            model=model,
            purpose="agent_chat",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        log.debug(
            "anthropic_chat_done",
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )


# ──────────────────────────────────────────────
# SSE 유틸리티
# ──────────────────────────────────────────────

def format_sse_chunk(text: str) -> str:
    """텍스트 청크를 SSE 포맷으로 변환.

    Returns:
        "data: {json}\\n\\n" 형식 문자열
    """
    payload = json.dumps({"type": "text", "text": text}, ensure_ascii=False)
    return f"data: {payload}\n\n"


def format_sse_done() -> str:
    """스트리밍 완료 SSE 이벤트."""
    payload = json.dumps({"type": "done"}, ensure_ascii=False)
    return f"data: {payload}\n\n"


def format_sse_error(error_msg: str) -> str:
    """오류 SSE 이벤트."""
    payload = json.dumps({"type": "error", "message": error_msg}, ensure_ascii=False)
    return f"data: {payload}\n\n"


def format_sse_tool_call(tool_id: str, name: str, input_dict: dict) -> str:
    """tool_use 블록을 SSE 포맷으로 변환."""
    payload = json.dumps(
        {"type": "tool_call", "id": tool_id, "name": name, "input": input_dict},
        ensure_ascii=False,
    )
    return f"data: {payload}\n\n"


def format_sse_tool_result(tool_id: str, name: str, summary: str) -> str:
    """tool_result 요약을 SSE 포맷으로 변환."""
    payload = json.dumps(
        {"type": "tool_result", "id": tool_id, "name": name, "summary": summary},
        ensure_ascii=False,
    )
    return f"data: {payload}\n\n"


def format_sse_thinking(text: str) -> str:
    """중간 텍스트(사고 과정)를 SSE thinking 포맷으로 변환."""
    payload = json.dumps({"type": "thinking", "text": text}, ensure_ascii=False)
    return f"data: {payload}\n\n"


# ──────────────────────────────────────────────
# Tool-use 헬퍼
# ──────────────────────────────────────────────

def _make_tool_summary(name: str, result: dict) -> str:
    """executor 결과에서 사람이 읽기 쉬운 요약 문자열 생성."""
    if "error" in result:
        return f"오류: {result['error']}"

    if name == "search_reports":
        total = result.get("total_count", len(result.get("reports", [])))
        return f"{total}건 검색됨"

    if name == "get_report_detail":
        count = len(result.get("reports", []))
        return f"{count}건 상세 조회"

    if name == "list_stocks":
        total = result.get("total_count", len(result.get("stocks", [])))
        return f"{total}개 종목"

    if name == "get_report_stats":
        period = result.get("period", {})
        date_from = period.get("from", "?")
        date_to = period.get("to", "?")
        return f"기간 {date_from}~{date_to} 통계"

    # 알 수 없는 도구 이름 — 일반 요약
    return "도구 실행 완료"


# ──────────────────────────────────────────────
# Agentic 루프 (tool-use)
# ──────────────────────────────────────────────

async def stream_agent_response(
    messages: list[dict],
    model: str,
    system: str,
    tools: list[dict],
    db_session: AsyncSession,
    max_iterations: int = 10,
) -> AsyncGenerator[str, None]:
    """tool-use 루프를 포함한 agentic SSE 스트리밍.

    각 반복에서 Anthropic non-streaming API를 호출하여 텍스트 및 tool_use 블록을 처리합니다.
    최종 텍스트는 format_sse_chunk, 중간 텍스트는 format_sse_thinking 으로 yield합니다.

    Args:
        messages: 대화 메시지 목록 ({"role": ..., "content": ...})
        model: 사용할 모델 ID
        system: 시스템 프롬프트
        tools: AGENT_TOOLS 형식의 tool 스키마 목록
        db_session: tool executor에 전달할 DB 세션
        max_iterations: 최대 반복 횟수 (무한 루프 방지)

    Yields:
        SSE 포맷 문자열
    """
    client = _get_agent_client()

    total_input_tokens = 0
    total_output_tokens = 0
    current_messages = list(messages)
    last_text: str = ""

    # CRITICAL 2: initialize before the loop so it is defined even when
    # max_iterations=0 (the for-body never executes) and in the finally block.
    iteration = 0

    try:
        for iteration in range(max_iterations):
            is_final = False  # 이 반복이 최종 응답인지 (end_turn)

            try:
                response = await client.messages.create(
                    model=model,
                    max_tokens=4096,
                    system=system,
                    tools=tools,
                    messages=current_messages,
                )
            except Exception as e:
                log.error("stream_agent_response_api_error", error=str(e), iteration=iteration)
                yield format_sse_error(str(e))
                yield format_sse_done()
                return  # finally still runs

            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens

            stop_reason = response.stop_reason  # "end_turn" | "tool_use" | "max_tokens" | ...

            # end_turn이면 이 반복이 최종
            if stop_reason == "end_turn":
                is_final = True

            # ── content 블록 처리 ──
            assistant_content_for_history: list[dict] = []
            tool_results_for_history: list[dict] = []

            for block in response.content:
                if block.type == "text":
                    text = block.text
                    last_text = text
                    if is_final:
                        if text:
                            yield format_sse_chunk(text)
                    else:
                        if text:
                            yield format_sse_thinking(text)
                    assistant_content_for_history.append({"type": "text", "text": text})

                elif block.type == "tool_use":
                    tool_id = block.id
                    tool_name = block.name
                    tool_input = block.input

                    yield format_sse_tool_call(tool_id, tool_name, tool_input)

                    assistant_content_for_history.append(
                        {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": tool_name,
                            "input": tool_input,
                        }
                    )

                    # 도구 실행
                    try:
                        tool_result = await execute_tool(tool_name, tool_input, db_session)
                    except Exception as exc:
                        log.error(
                            "stream_agent_response_tool_error",
                            tool=tool_name,
                            error=str(exc),
                        )
                        # CRITICAL 5: rollback poisoned session before continuing
                        await db_session.rollback()
                        tool_result = {"error": str(exc)}

                    summary = _make_tool_summary(tool_name, tool_result)
                    yield format_sse_tool_result(tool_id, tool_name, summary)

                    tool_results_for_history.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": json.dumps(tool_result, ensure_ascii=False, default=str),
                        }
                    )

            if is_final:
                yield format_sse_done()
                break

            # CRITICAL 3: max_tokens — text was already emitted as thinking/chunk;
            # treat as final without re-emitting.
            if stop_reason == "max_tokens":
                yield format_sse_done()
                break

            # tool_use stop_reason — messages에 assistant + tool_result 추가 후 계속
            if stop_reason == "tool_use":
                current_messages.append(
                    {"role": "assistant", "content": assistant_content_for_history}
                )
                current_messages.append(
                    {"role": "user", "content": tool_results_for_history}
                )
                continue

            # 예상치 못한 stop_reason — 마지막 텍스트를 최종으로 처리
            if last_text:
                yield format_sse_chunk(last_text)
            yield format_sse_done()
            break

        else:
            # max_iterations 소진
            log.warning("stream_agent_response_max_iterations", max_iterations=max_iterations)
            if last_text:
                yield format_sse_chunk(last_text)
            yield format_sse_done()

    finally:
        # CRITICAL 1: always record usage, even when an early return fires above.
        await record_llm_usage(
            model=model,
            purpose="agent_chat",
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
        )

        log.debug(
            "stream_agent_response_done",
            model=model,
            total_input_tokens=total_input_tokens,
            total_output_tokens=total_output_tokens,
            iterations=iteration + 1,
        )


# ──────────────────────────────────────────────
# 스트리밍 SSE 변환기
# ──────────────────────────────────────────────

async def stream_to_sse(
    provider: LLMChatProvider,
    messages: list[dict],
    model: str,
    system: str | None = None,
    max_tokens: int = 4096,
) -> AsyncIterator[str]:
    """LLM 스트리밍 응답을 SSE 포맷으로 변환하는 제너레이터.

    Args:
        provider: LLMChatProvider 구현체
        messages: 대화 메시지 목록
        model: 사용할 모델 ID
        system: 시스템 프롬프트
        max_tokens: 최대 출력 토큰

    Yields:
        SSE 포맷 문자열 ("data: {...}\\n\\n")
    """
    try:
        async for chunk in provider.stream_chat(
            messages=messages,
            model=model,
            system=system,
            max_tokens=max_tokens,
        ):
            if chunk:
                yield format_sse_chunk(chunk)
        yield format_sse_done()
    except Exception as e:
        log.error("stream_to_sse_error", error=str(e))
        yield format_sse_error(str(e))
        yield format_sse_done()


# ──────────────────────────────────────────────
# 기본 프로바이더 팩토리
# ──────────────────────────────────────────────

def get_default_provider() -> AnthropicChatProvider:
    """기본 AnthropicChatProvider 인스턴스 반환."""
    return AnthropicChatProvider(
        api_key=settings.anthropic_api_key,
        timeout=settings.llm_timeout,
    )
