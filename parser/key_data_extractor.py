"""③ 키 데이터 추출 — 중복 파악용 메타데이터를 첫 페이지 텍스트에서 추출.

Gemini Flash-Lite를 사용하여 저비용으로 증권사, 애널리스트, 날짜, 종목명, 리포트 타입을 추출.
Layer2 전에 실행하여 중복 리포트를 조기 필터링할 수 있게 함.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog

from config.settings import settings
from db.models import calc_cost_usd
from storage.llm_usage_repo import record_llm_usage

log = structlog.get_logger(__name__)

_EXTRACT_PROMPT = """\
아래는 한국 증권사 리서치 리포트 PDF의 첫 페이지 텍스트입니다.
다음 필드를 JSON으로 추출하세요. 없으면 null.

- broker: 증권사명 (예: "삼성증권", "NH투자증권"). 최대 50자.
- analyst: 기업분석/산업분석이면 대표 애널리스트 1명 이름 (예: "홍길동"). 주간전략/퀀트/매크로면 팀명 (예: "투자전략팀"). 최대 20자.
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


def _get_first_page_text(pdf_path) -> str | None:
    """PDF 첫 페이지 텍스트만 추출."""
    try:
        import pymupdf
        doc = pymupdf.open(pdf_path)
        if len(doc) == 0:
            doc.close()
            return None
        text = doc[0].get_text()
        doc.close()
        return text if text.strip() else None
    except Exception as e:
        log.warning("first_page_extract_failed", error=str(e))
        return None


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

    first_page = _get_first_page_text(pdf_path)
    if not first_page:
        return None

    try:
        from google import genai
        import json

        client = genai.Client(api_key=settings.gemini_api_key)
        response = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=settings.gemini_model,
                contents=[{"parts": [{"text": f"{_EXTRACT_PROMPT}\n\n---\n{first_page[:3000]}"}]}],
                config={"response_mime_type": "application/json"},
            ),
            timeout=30,
        )

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

        result = KeyDataResult(
            broker=data.get("broker"),
            analyst=data.get("analyst"),
            date=data.get("date"),
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
            broker=result.broker,
            stock=result.stock_name,
            cost_usd=float(calc_cost_usd(model, input_tokens, output_tokens)),
        )
        return result

    except Exception as e:
        log.warning("key_data_extract_failed", error=str(e), report_id=report_id)
        return None
