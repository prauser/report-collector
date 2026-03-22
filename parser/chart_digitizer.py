"""차트/테이블 이미지를 마크다운 텍스트로 수치화하는 모듈.

Gemini Flash를 사용하여 이미지 속 수치 데이터를 마크다운 테이블로 변환.
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from decimal import Decimal

import structlog

from config.settings import settings
from db.models import calc_cost_usd
from parser.image_extractor import ExtractedImage
from parser.rate_limit import RateLimitGate
from storage.llm_usage_repo import record_llm_usage

log = structlog.get_logger(__name__)

# Global backoff gate — when one chart call hits a Gemini rate limit all callers pause
_gemini_chart_gate = RateLimitGate("gemini_chart")

_DIGITIZE_PROMPT = """\
이 이미지는 한국 증권사 리서치 리포트에서 추출한 차트 또는 테이블입니다.
이미지의 모든 수치 데이터를 마크다운 형식으로 정확하게 변환하세요.

## 규칙
- 테이블: 마크다운 테이블로 변환. 모든 행/열/수치를 빠짐없이 포함.
- 차트(막대/선/원형 등): 데이터를 마크다운 테이블로 재구성. 축 라벨, 범례, 수치를 포함.
- 단위(억원, %, 배 등)를 반드시 표기.
- 원본에 없는 수치를 추가하거나 추정하지 마세요.
- 제목이 있으면 ##로 표기.
- 이미지가 차트/테이블이 아니면(회사 로고, 장식 등) "N/A"만 반환.
"""

# 동시 호출 제한
_SEMAPHORE = asyncio.Semaphore(5)


@dataclass
class DigitizeResult:
    """수치화 결과."""
    texts: list[str] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: Decimal = Decimal("0")
    image_count: int = 0
    success_count: int = 0


_GEMINI_TIMEOUT = 60  # 초


async def _digitize_single(image: ExtractedImage) -> tuple[str | None, int, int]:
    """단일 이미지를 Gemini로 수치화. 타임아웃 적용."""
    from google import genai
    from google.genai.errors import ClientError as GeminiClientError

    await _gemini_chart_gate.wait()  # gate check BEFORE acquiring semaphore slot
    async with _SEMAPHORE:
        try:
            client = genai.Client(api_key=settings.gemini_api_key)
            b64_data = base64.b64encode(image.image_bytes).decode()

            # Up to 2 attempts; on rate limit trigger the global gate and retry once
            last_exc: Exception | None = None
            response = None
            for attempt in range(2):
                try:
                    response = await asyncio.wait_for(
                        client.aio.models.generate_content(
                            model=settings.gemini_model,
                            contents=[
                                {
                                    "parts": [
                                        {"text": _DIGITIZE_PROMPT},
                                        {
                                            "inline_data": {
                                                "mime_type": "image/png",
                                                "data": b64_data,
                                            }
                                        },
                                    ]
                                }
                            ],
                        ),
                        timeout=_GEMINI_TIMEOUT,
                    )
                    break  # success
                except GeminiClientError as e:
                    if getattr(e, "code", None) != 429:
                        raise  # non-rate-limit error — propagate immediately
                    last_exc = e
                    log.warning(
                        "digitize_rate_limit",
                        page=image.page_num,
                        source=image.source,
                        attempt=attempt + 1,
                        error=str(e),
                    )
                    await _gemini_chart_gate.trigger_backoff(60.0)
                    await _gemini_chart_gate.wait()  # wait for gate to reopen before retry
            else:
                raise last_exc  # both attempts exhausted

            text = response.text or ""
            input_tokens = response.usage_metadata.prompt_token_count or 0
            output_tokens = response.usage_metadata.candidates_token_count or 0

            # N/A 응답 필터링
            if text.strip().upper() == "N/A":
                return None, input_tokens, output_tokens

            return text.strip(), input_tokens, output_tokens

        except asyncio.TimeoutError:
            log.warning(
                "digitize_timeout",
                page=image.page_num,
                source=image.source,
                timeout=_GEMINI_TIMEOUT,
            )
            return None, 0, 0
        except Exception as e:
            log.warning(
                "digitize_failed",
                page=image.page_num,
                source=image.source,
                error=str(e),
            )
            return None, 0, 0


async def digitize_charts(
    images: list[ExtractedImage],
    report_id: int | None = None,
    channel: str = "",
) -> DigitizeResult:
    """
    이미지 리스트를 Gemini로 수치화.

    Args:
        images: ExtractedImage 리스트
        report_id: 연결할 리포트 ID
        channel: 소스 채널

    Returns:
        DigitizeResult
    """
    if not images:
        return DigitizeResult()

    if not settings.gemini_api_key:
        log.warning("gemini_api_key_not_set")
        return DigitizeResult(image_count=len(images))

    # 병렬 처리
    tasks = [_digitize_single(img) for img in images]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    digitized = DigitizeResult(image_count=len(images))

    for i, result in enumerate(results):
        if isinstance(result, Exception):
            log.warning("digitize_exception", page=images[i].page_num, error=str(result))
            continue

        text, in_tokens, out_tokens = result
        digitized.total_input_tokens += in_tokens
        digitized.total_output_tokens += out_tokens

        if text:
            page_label = f"[페이지 {images[i].page_num + 1}]"
            digitized.texts.append(f"{page_label}\n{text}")
            digitized.success_count += 1

    model = settings.gemini_model
    digitized.total_cost_usd = calc_cost_usd(
        model, digitized.total_input_tokens, digitized.total_output_tokens,
    )

    # 비용 기록
    if digitized.total_input_tokens > 0:
        await record_llm_usage(
            model=model,
            purpose="chart_digitize",
            input_tokens=digitized.total_input_tokens,
            output_tokens=digitized.total_output_tokens,
            source_channel=channel,
            report_id=report_id,
        )

    log.info(
        "charts_digitized",
        report_id=report_id,
        images=len(images),
        success=digitized.success_count,
        cost_usd=float(digitized.total_cost_usd),
    )
    return digitized
