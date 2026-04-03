"""③ 키 데이터 추출 — 중복 파악용 메타데이터를 첫 페이지 텍스트에서 추출.

Gemini Flash-Lite를 사용하여 저비용으로 증권사, 애널리스트, 날짜, 종목명, 리포트 타입을 추출.
Layer2 전에 실행하여 중복 리포트를 조기 필터링할 수 있게 함.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import date as _date, timedelta

import structlog

from config.settings import settings
from db.models import calc_cost_usd
from parser.rate_limit import RateLimitGate
from storage.llm_usage_repo import record_llm_usage

log = structlog.get_logger(__name__)

# Module-level lazy Gemini client (created once, reused across calls)
_gemini_client = None


def _get_gemini_client():
    """Return the module-level Gemini client, creating it on first call."""
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=settings.gemini_api_key)
    return _gemini_client


# Global backoff gate — when one key-data call hits a Gemini rate limit all callers pause
_gemini_keydata_gate = RateLimitGate("gemini_keydata")

_EXTRACT_PROMPT = """\
아래는 한국 증권사 리서치 리포트 PDF의 첫 페이지 텍스트입니다.
다음 필드를 JSON으로 추출하세요. 없으면 null.

- broker: 증권사명 (예: "삼성증권", "NH투자증권"). 최대 50자.
- analyst: 기업분석/산업분석이면 대표 애널리스트 1명 이름 (예: "홍길동"). 주간전략/퀀트/매크로면 팀명 (예: "투자전략팀"). 특정 불가하면 "Unknown". 최대 20자.
- date: 리포트 발행일 (YYYY-MM-DD)
- stock_name: 주요 종목명 (종목 리포트일 때). 최대 30자.
- stock_code: 종목코드 6자리
- title: 리포트 제목
- report_type: 리포트 유형. 다음 중 택1: 기업분석/산업분석/매크로/실적리뷰/퀀트/주간전략/기타
- opinion: 투자의견. 다음 중 택1: 매수/중립/비중축소/매도/Trading Buy. 없으면 null.
- target_price: 목표주가 (숫자만)

정확히 이 필드만 포함한 JSON 하나만 출력하세요. 설명 불필요.
"""


@dataclass
class KeyDataResult:
    """③ 키 데이터 추출 결과."""
    broker: str | None = None
    analyst: str | None = None
    date: str | None = None
    stock_name: str | None = None
    stock_code: str | None = None
    title: str | None = None
    report_type: str | None = None
    opinion: str | None = None
    target_price: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0


def _get_first_pages_text_sync(pdf_path, max_pages: int = 3) -> str | None:
    """PDF 첫 N페이지 텍스트 추출 (기본 3페이지)."""
    try:
        import pymupdf
        doc = pymupdf.open(pdf_path)
        if len(doc) == 0:
            doc.close()
            return None
        pages = []
        for i in range(min(max_pages, len(doc))):
            t = doc[i].get_text()
            if t.strip():
                pages.append(t)
        doc.close()
        return "\n".join(pages) if pages else None
    except Exception as e:
        log.warning("first_page_extract_failed", error=str(e))
        return None


_DATE_PATTERNS = [
    # 2025.03.15 / 2025. 03. 15 / 2025.3.15
    re.compile(r"(20[2-3]\d)\.\s*(\d{1,2})\.\s*(\d{1,2})"),
    # 2025-03-15
    re.compile(r"(20[2-3]\d)-(\d{1,2})-(\d{1,2})"),
    # 2025/03/15
    re.compile(r"(20[2-3]\d)/(\d{1,2})/(\d{1,2})"),
    # 2025년 3월 15일
    re.compile(r"(20[2-3]\d)\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"),
]


def _extract_date_regex(text: str) -> _date | None:
    """PDF 텍스트에서 정규식으로 리포트 발행일 추출.

    텍스트에서 가장 먼저 등장하는 유효 날짜를 반환 (표지 발행일이 보통 최상단).
    """
    max_date = _date.today() + timedelta(days=7)
    min_date = _date(2020, 1, 1)

    # (position, date) 튜플로 수집하여 가장 앞에 등장하는 날짜 선택
    first_match: tuple[int, _date] | None = None

    for pat in _DATE_PATTERNS:
        for m in pat.finditer(text):
            try:
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                dt = _date(y, mo, d)
                if min_date <= dt <= max_date:
                    pos = m.start()
                    if first_match is None or pos < first_match[0]:
                        first_match = (pos, dt)
            except (ValueError, TypeError):
                continue

    return first_match[1] if first_match else None


_FIRST_PAGE_TIMEOUT = 30  # seconds


async def extract_key_data(
    pdf_path,
    report_id: int | None = None,
    channel: str = "",
) -> KeyDataResult | None:
    """
    PDF 첫 페이지에서 키 데이터를 추출.

    Returns:
        KeyDataResult 또는 None (API 키 없거나 실패 시)
    """
    if not settings.gemini_api_key:
        return None

    try:
        first_pages = await asyncio.wait_for(
            asyncio.to_thread(_get_first_pages_text_sync, pdf_path),
            timeout=_FIRST_PAGE_TIMEOUT,
        )
    except (asyncio.TimeoutError, Exception) as e:
        log.warning("first_page_extract_timeout", path=str(pdf_path), error=str(e))
        return None
    if not first_pages:
        return None

    # 정규식으로 날짜 먼저 추출 (Gemini보다 정확)
    regex_date = _extract_date_regex(first_pages)

    try:
        from google.genai.errors import ClientError as GeminiClientError
        import json

        client = _get_gemini_client()

        # Up to 2 attempts; on rate limit trigger the global gate and retry once
        last_exc: Exception | None = None
        response = None
        for attempt in range(2):
            await _gemini_keydata_gate.wait()
            try:
                response = await asyncio.wait_for(
                    client.aio.models.generate_content(
                        model=settings.gemini_model,
                        contents=[{"parts": [{"text": f"{_EXTRACT_PROMPT}\n\n---\n{first_pages[:3000]}"}]}],
                        config={"response_mime_type": "application/json"},
                    ),
                    timeout=30,
                )
                break  # success
            except GeminiClientError as e:
                if getattr(e, "code", None) != 429:
                    raise  # non-rate-limit client error — propagate immediately
                last_exc = e
                log.warning(
                    "key_data_rate_limit",
                    attempt=attempt + 1,
                    error=str(e),
                    report_id=report_id,
                )
                await _gemini_keydata_gate.trigger_backoff(60.0)
                # loop continues for second attempt
        else:
            raise last_exc  # both attempts exhausted

        text = response.text or ""
        input_tokens = response.usage_metadata.prompt_token_count or 0
        output_tokens = response.usage_metadata.candidates_token_count or 0

        # JSON 파싱
        data = json.loads(text)

        # 비용 기록
        model = settings.gemini_model
        await record_llm_usage(
            model=model,
            purpose="key_data_extract",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            source_channel=channel,
            report_id=report_id,
        )

        # target_price 정수 변환
        tp = data.get("target_price")
        if tp is not None:
            try:
                tp = int(str(tp).replace(",", ""))
            except (ValueError, TypeError):
                tp = None

        # 날짜 결정: 정규식 추출 > Gemini 추출
        final_date: str | None = None
        if regex_date:
            final_date = regex_date.isoformat()
        elif data.get("date"):
            final_date = data["date"]

        # Gemini 날짜도 파싱하여 정규화 비교 (포맷 차이로 인한 false positive 방지)
        gemini_parsed: _date | None = None
        if data.get("date"):
            try:
                gemini_parsed = _date.fromisoformat(data["date"])
            except (ValueError, TypeError):
                pass

        if regex_date and gemini_parsed and gemini_parsed != regex_date:
            log.info(
                "date_regex_override",
                report_id=report_id,
                gemini_date=data["date"],
                regex_date=regex_date.isoformat(),
            )

        result = KeyDataResult(
            broker=data.get("broker"),
            analyst=data.get("analyst"),
            date=final_date,
            stock_name=data.get("stock_name"),
            stock_code=data.get("stock_code"),
            title=data.get("title"),
            report_type=data.get("report_type"),
            opinion=data.get("opinion"),
            target_price=tp,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

        log.info(
            "key_data_extracted",
            report_id=report_id,
            model=model,
            broker=result.broker,
            stock=result.stock_name,
            cost_usd=float(calc_cost_usd(model, input_tokens, output_tokens)),
        )
        return result

    except Exception as e:
        log.warning("key_data_extract_failed", error=str(e), report_id=report_id)
        if regex_date:
            return KeyDataResult(date=regex_date.isoformat())
        return None
