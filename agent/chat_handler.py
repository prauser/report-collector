"""채팅 핸들러 — LLM 스트리밍 호출 + SSE 변환 + usage 기록."""
from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import AsyncIterator, Protocol, runtime_checkable

import structlog
from anthropic import AsyncAnthropic

from config.settings import settings
from storage.llm_usage_repo import record_llm_usage

log = structlog.get_logger(__name__)


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
