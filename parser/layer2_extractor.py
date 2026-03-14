"""Layer 2 구조화 추출 — 리포트 분류 + 체인 스키마 추출.

S2b(메타추출) + Stage5(PDF분석)를 통합한 단일 추출 모듈.
Sonnet 1회 호출로 메타데이터 + 투자 논리 체인 + 연관 종목/섹터/키워드를 모두 추출.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import structlog
from anthropic import AsyncAnthropic, RateLimitError, APIConnectionError
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config.settings import settings
from db.models import calc_cost_usd
from storage.llm_usage_repo import record_llm_usage

log = structlog.get_logger(__name__)

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            timeout=60,
        )
    return _client


# ──────────────────────────────────────────────
# 결과 데이터 클래스
# ──────────────────────────────────────────────

@dataclass
class Layer2Result:
    """Layer 2 추출 결과."""
    # 리포트 분류
    report_category: str  # stock / industry / macro

    # 전체 분석 데이터 (JSONB로 저장)
    analysis_data: dict = field(default_factory=dict)

    # 메타데이터 (reports 테이블 업데이트용)
    meta: dict = field(default_factory=dict)

    # 연관 종목 리스트
    stock_mentions: list[dict] = field(default_factory=list)

    # 연관 섹터 리스트
    sector_mentions: list[dict] = field(default_factory=list)

    # 키워드 리스트
    keywords: list[dict] = field(default_factory=list)

    # 추출 품질
    extraction_quality: str = "medium"  # high / medium / low / truncated

    # Markdown truncation 정보
    markdown_truncated: bool = False
    markdown_original_chars: int = 0

    # LLM 사용량
    llm_model: str = ""
    llm_cost_usd: Decimal = Decimal("0")
    input_tokens: int = 0
    output_tokens: int = 0


# ──────────────────────────────────────────────
# LLM 프롬프트 및 툴 스키마
# ──────────────────────────────────────────────

_SYSTEM_PROMPT = """\
당신은 한국 증권사 리서치 리포트 분석 전문가입니다.
리포트 본문(텍스트 또는 마크다운)을 분석하여 구조화된 정보를 추출합니다.

## 분류 기준
- stock: 특정 종목 분석 리포트 (기업분석, 실적리뷰, 기업메모 등)
- industry: 산업/섹터 분석 리포트 (업종분석, 산업동향 등)
- macro: 거시경제/정책/시황 리포트 (경제전망, 금리, 환율 등)

## 추출 원칙
1. 애널리스트의 논리 흐름(인과관계 체인)을 보존하세요.
2. 체인의 각 step은 trigger → mechanism → impact 순서로 연결됩니다.
3. 메타데이터(증권사, 애널리스트, 종목, 투자의견 등)는 정확히 추출하세요.
4. 언급된 종목, 섹터, 키워드를 빠짐없이 추출하세요.
"""

_EXTRACT_TOOL = {
    "name": "extract_layer2",
    "description": "증권사 리포트에서 Layer 2 구조화 데이터를 추출합니다.",
    "input_schema": {
        "type": "object",
        "properties": {
            "report_category": {
                "type": "string",
                "enum": ["stock", "industry", "macro"],
                "description": "리포트 분류",
            },
            "meta": {
                "type": "object",
                "description": "리포트 메타데이터",
                "properties": {
                    "broker": {"type": "string", "description": "증권사명"},
                    "analyst": {"type": "string", "description": "애널리스트 이름"},
                    "title": {"type": "string", "description": "리포트 제목"},
                    "report_type": {"type": "string", "description": "리포트 유형 (기업분석, 산업분석 등)"},
                    "stock_name": {"type": "string", "description": "주요 종목명 (종목 리포트일 때)"},
                    "stock_code": {"type": "string", "description": "종목코드 6자리"},
                    "sector": {"type": "string", "description": "섹터/산업"},
                    "opinion": {"type": "string", "description": "투자의견 (매수, 중립 등)"},
                    "target_price": {"type": "integer", "description": "목표주가"},
                    "prev_opinion": {"type": "string", "description": "이전 투자의견"},
                    "prev_target_price": {"type": "integer", "description": "이전 목표주가"},
                },
            },
            "target": {
                "type": "object",
                "description": "분석 대상 정보 (종목: ticker/name, 산업: sector, 매크로: topic)",
                "properties": {
                    "ticker": {"type": "string"},
                    "name": {"type": "string"},
                    "sector": {"type": "string"},
                    "topic": {"type": "string"},
                },
            },
            "opinion": {
                "type": "object",
                "description": "투자의견 상세 (종목/산업 리포트)",
                "properties": {
                    "rating": {"type": "string", "description": "투자의견"},
                    "target_price": {"type": "integer"},
                    "prev_rating": {"type": "string"},
                    "prev_target_price": {"type": "integer"},
                    "change_reason": {"type": "string", "description": "의견 변경 이유"},
                },
            },
            "thesis": {
                "type": "object",
                "description": "핵심 투자 논리",
                "properties": {
                    "summary": {"type": "string", "description": "핵심 논지 2~3문장"},
                    "sentiment": {
                        "type": "number",
                        "description": "감성 점수 (-1.0 ~ 1.0)",
                    },
                },
            },
            "chain": {
                "type": "array",
                "description": "인과관계 체인 (논리 흐름)",
                "items": {
                    "type": "object",
                    "properties": {
                        "step": {
                            "type": "string",
                            "enum": [
                                "trigger", "mechanism",
                                "demand_transmission", "supply_dynamics", "pricing_impact",
                                "financial_impact", "valuation_impact",
                                "structural_risk", "uncertainty",
                                "data_signal", "policy_logic", "market_transmission", "local_impact",
                            ],
                        },
                        "text": {"type": "string", "description": "이 단계의 핵심 내용 (1~2문장)"},
                        "direction": {
                            "type": "string",
                            "enum": ["positive", "negative", "neutral", "mixed"],
                        },
                        "confidence": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                    },
                    "required": ["step", "text"],
                },
            },
            "financials": {
                "type": "object",
                "description": "재무 추정 (종목 리포트)",
                "properties": {
                    "earnings_quarter": {"type": "string"},
                    "revenue": {"type": "string", "description": "매출액 추정"},
                    "operating_profit": {"type": "string", "description": "영업이익 추정"},
                    "eps": {"type": "string", "description": "EPS 추정"},
                    "key_metrics": {
                        "type": "object",
                        "description": "주요 재무지표 (PER, PBR, ROE 등)",
                    },
                },
            },
            "stock_mentions": {
                "type": "array",
                "description": "언급된 종목 리스트",
                "items": {
                    "type": "object",
                    "properties": {
                        "stock_code": {"type": "string", "description": "종목코드 6자리"},
                        "company_name": {"type": "string"},
                        "mention_type": {
                            "type": "string",
                            "enum": ["primary", "implication", "related"],
                            "description": "primary=분석 대상, implication=영향받는 종목, related=맥락상 언급",
                        },
                        "impact": {
                            "type": "string",
                            "enum": ["positive", "negative", "neutral", "mixed"],
                        },
                        "relevance_score": {
                            "type": "number",
                            "description": "관련도 0.0~1.0",
                        },
                    },
                    "required": ["company_name", "mention_type"],
                },
            },
            "sector_mentions": {
                "type": "array",
                "description": "언급된 섹터 리스트",
                "items": {
                    "type": "object",
                    "properties": {
                        "sector": {"type": "string"},
                        "mention_type": {
                            "type": "string",
                            "enum": ["primary", "implication"],
                        },
                        "impact": {
                            "type": "string",
                            "enum": ["positive", "negative", "neutral", "mixed"],
                        },
                    },
                    "required": ["sector", "mention_type"],
                },
            },
            "keywords": {
                "type": "array",
                "description": "핵심 키워드 5~15개",
                "items": {
                    "type": "object",
                    "properties": {
                        "keyword": {"type": "string"},
                        "keyword_type": {
                            "type": "string",
                            "enum": ["industry", "macro", "product", "policy", "event"],
                        },
                    },
                    "required": ["keyword"],
                },
            },
            "extraction_quality": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "추출 품질 자체 평가. high=마크다운 풍부, medium=텍스트만, low=정보 부족",
            },
        },
        "required": ["report_category", "meta", "thesis", "chain", "extraction_quality"],
    },
}


# ──────────────────────────────────────────────
# LLM 호출
# ──────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(settings.llm_max_retries + 1),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((RateLimitError, APIConnectionError)),
    reraise=True,
)
async def _call_extract(user_content: str):
    """Layer2 추출 LLM 호출."""
    client = _get_client()
    response = await client.messages.create(
        model=settings.llm_pdf_model,  # Sonnet
        max_tokens=4096,
        system=_SYSTEM_PROMPT,
        tools=[_EXTRACT_TOOL],
        tool_choice={"type": "tool", "name": "extract_layer2"},
        messages=[{"role": "user", "content": user_content}],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "extract_layer2":
            return block.input, response
    return None, response


# ──────────────────────────────────────────────
# 공개 인터페이스
# ──────────────────────────────────────────────

async def extract_layer2(
    text: str,
    markdown: str | None = None,
    channel: str = "",
    report_id: int | None = None,
) -> Layer2Result | None:
    """
    Layer 2 구조화 추출.

    Args:
        text: 텔레그램 메시지 원문 (raw_text)
        markdown: PDF → Markdown 변환 결과 (없으면 텍스트만으로 추출)
        channel: 소스 채널
        report_id: 연결할 리포트 ID

    Returns:
        Layer2Result 또는 None (LLM 비활성/실패 시)
    """
    if not settings.analysis_enabled or not settings.anthropic_api_key:
        return None

    # 컨텍스트 구성
    _MD_LIMIT = 30_000
    parts = [f"[채널: {channel}]"]
    parts.append(f"\n[텔레그램 메시지]\n{text}")
    md_was_truncated = False
    md_original_chars = 0
    if markdown:
        md_original_chars = len(markdown)
        md_was_truncated = md_original_chars > _MD_LIMIT
        md_text = markdown[:_MD_LIMIT]
        parts.append(f"\n[PDF 마크다운 본문]\n{md_text}")
        if md_was_truncated:
            log.warning(
                "markdown_truncated",
                report_id=report_id,
                original_chars=md_original_chars,
                limit=_MD_LIMIT,
            )

    user_content = "\n".join(parts)

    try:
        result, response = await _call_extract(user_content)
    except Exception as e:
        log.warning("layer2_extract_failed", error=str(e), report_id=report_id)
        return None

    usage = response.usage
    model = settings.llm_pdf_model
    cost = calc_cost_usd(model, usage.input_tokens, usage.output_tokens)

    await record_llm_usage(
        model=model,
        purpose="layer2_extract",
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        source_channel=channel,
        report_id=report_id,
    )

    if result is None:
        log.warning("layer2_no_result", report_id=report_id)
        return None

    # analysis_data 구성 (stock_mentions/sector_mentions/keywords 제외 — 별도 테이블로)
    analysis_data = {}
    for key in ("target", "opinion", "thesis", "chain", "financials"):
        if key in result:
            analysis_data[key] = result[key]
    # meta도 analysis_data에 포함 (검색용)
    if "meta" in result:
        analysis_data["meta"] = result["meta"]

    quality = result.get("extraction_quality", "medium")
    if md_was_truncated:
        quality = "truncated"

    layer2 = Layer2Result(
        report_category=result.get("report_category", "stock"),
        analysis_data=analysis_data,
        meta=result.get("meta", {}),
        stock_mentions=result.get("stock_mentions", []),
        sector_mentions=result.get("sector_mentions", []),
        keywords=result.get("keywords", []),
        extraction_quality=quality,
        markdown_truncated=md_was_truncated,
        markdown_original_chars=md_original_chars,
        llm_model=model,
        llm_cost_usd=cost,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
    )

    log.info(
        "layer2_extracted",
        report_id=report_id,
        category=layer2.report_category,
        quality=layer2.extraction_quality,
        chain_steps=len(analysis_data.get("chain", [])),
        stocks=len(layer2.stock_mentions),
    )
    return layer2
