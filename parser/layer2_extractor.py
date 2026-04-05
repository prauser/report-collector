"""Layer 2 구조화 추출 — 리포트 분류 + 체인 스키마 추출.

S2b(메타추출) + Stage5(PDF분석)를 통합한 단일 추출 모듈.
Sonnet 1회 호출로 메타데이터 + 투자 논리 체인 + 연관 종목/섹터/키워드를 모두 추출.

지원 모드:
- 실시간 (listener): Prompt Caching 적용 개별 호출
- 배치 (backfill): Batch API (50% 할인) + Prompt Caching
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import structlog
from anthropic import AsyncAnthropic, RateLimitError, APIConnectionError
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config.settings import settings
from db.models import calc_cost_usd
from storage.llm_usage_repo import record_llm_usage

log = structlog.get_logger(__name__)

_PENDING_BATCHES_PATH = Path(__file__).parent.parent / "logs" / "pending_batches.jsonl"


def _save_pending_batch(batch_id: str, custom_ids: list[str]) -> None:
    """Append a JSONL line recording a submitted batch for crash recovery."""
    try:
        _PENDING_BATCHES_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "batch_id": str(batch_id),
            "submitted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            "count": len(custom_ids),
            "custom_ids": [str(c) for c in custom_ids],
        }
        with open(_PENDING_BATCHES_PATH, "a", encoding="utf-8") as fp:
            fp.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as e:
        log.warning("pending_batch_save_failed", batch_id=batch_id, error=str(e))


def _remove_pending_batch(batch_id: str) -> None:
    """Remove a batch_id entry from the pending batches file after successful completion."""
    if not _PENDING_BATCHES_PATH.exists():
        return
    try:
        lines = _PENDING_BATCHES_PATH.read_text(encoding="utf-8").splitlines()
        kept = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                kept.append(line)
                continue
            if entry.get("batch_id") != batch_id:
                kept.append(line)
        # Write back atomically via temp file + rename
        tmp_path = _PENDING_BATCHES_PATH.with_suffix(".jsonl.tmp")
        tmp_path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        tmp_path.replace(_PENDING_BATCHES_PATH)
    except OSError as e:
        log.warning("pending_batch_remove_failed", batch_id=batch_id, error=str(e))

_client: AsyncAnthropic | None = None


def _get_client() -> AsyncAnthropic:
    global _client
    if _client is None:
        _client = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            timeout=300,
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

    # 분류 확신도 및 보조 분류
    category_confidence: float = 1.0  # 0.0~1.0, 1.0=명확, 0.5 이하=경계 사례
    secondary_category: str | None = None  # stock / industry / macro, 선택적

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
            "category_confidence": {
                "type": "number",
                "description": "분류 확신도. 1.0=명확, 0.5 이하=경계 사례",
                "minimum": 0.0,
                "maximum": 1.0,
            },
            "secondary_category": {
                "type": ["string", "null"],
                "enum": ["stock", "industry", "macro", None],
                "description": "주 분류와 다른 관점이 있을 때. 없으면 null",
            },
            "meta": {
                "type": "object",
                "description": "리포트 메타데이터",
                "properties": {
                    "broker": {"type": "string", "description": "증권사명 (예: 삼성증권). 최대 50자"},
                    "analyst": {"type": "string", "description": "기업/산업분석이면 대표 애널리스트 1명 (예: 홍길동). 주간전략/퀀트/매크로면 팀명 (예: 투자전략팀). 특정 불가하면 Unknown. 최대 20자"},
                    "title": {"type": "string", "description": "리포트 제목"},
                    "report_type": {"type": "string", "enum": ["기업분석", "산업분석", "매크로", "실적리뷰", "퀀트", "주간전략", "기타"], "description": "리포트 유형"},
                    "stock_name": {"type": "string", "description": "주요 종목명. 최대 30자"},
                    "stock_code": {"type": "string", "description": "종목코드 6자리"},
                    "sector": {"type": "string", "description": "섹터/산업. 최대 30자"},
                    "opinion": {"type": "string", "enum": ["매수", "중립", "비중축소", "매도", "Trading Buy"], "description": "투자의견. 없으면 생략"},
                    "target_price": {"type": "integer", "description": "목표주가"},
                    "prev_opinion": {"type": "string", "enum": ["매수", "중립", "비중축소", "매도", "Trading Buy"], "description": "이전 투자의견"},
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
        "required": ["report_category", "category_confidence", "meta", "thesis", "chain", "extraction_quality"],
    },
}

# Prompt Caching — 툴 스키마에 cache_control 추가하여 시스템프롬프트+툴 prefix 캐싱
_EXTRACT_TOOL_CACHED = {**_EXTRACT_TOOL, "cache_control": {"type": "ephemeral"}}


# ──────────────────────────────────────────────
# 컨텐츠 빌더
# ──────────────────────────────────────────────

def build_user_content(
    text: str,
    markdown: str | None = None,
    chart_texts: list[str] | None = None,
    channel: str = "",
) -> tuple[str, bool, int]:
    """Layer2 추출용 user content 문자열 생성.

    markdown 전문 + chart_texts 전문을 그대로 전달 (제한 없음).

    Returns: (user_content, md_was_truncated, md_original_chars)
    """
    parts = []

    # markdown 먼저 (리포트 초반에 투자의견/요약이 있어 attention 확보)
    md_original_chars = 0
    if markdown:
        md_original_chars = len(markdown)
        parts.append(f"[PDF 마크다운 본문]\n{markdown}")

    # chart_texts 끝에 배치 (정확한 수치 데이터, recency bias 활용)
    if chart_texts:
        charts_block = "\n\n".join(chart_texts)
        parts.append(f"\n[차트/테이블 수치화 데이터]\n{charts_block}")

    return "\n".join(parts), False, md_original_chars


# ──────────────────────────────────────────────
# LLM 호출 (실시간 — Prompt Caching)
# ──────────────────────────────────────────────

@retry(
    stop=stop_after_attempt(settings.llm_max_retries + 1),
    wait=wait_exponential(multiplier=1, min=2, max=30),
    retry=retry_if_exception_type((RateLimitError, APIConnectionError)),
    reraise=True,
)
async def _call_extract(user_content: str):
    """Layer2 추출 LLM 호출 (Prompt Caching 적용)."""
    client = _get_client()
    response = await client.messages.create(
        model=settings.llm_pdf_model,
        max_tokens=8192,
        system=_SYSTEM_PROMPT,
        tools=[_EXTRACT_TOOL_CACHED],
        tool_choice={"type": "tool", "name": "extract_layer2"},
        messages=[{"role": "user", "content": user_content}],
    )
    for block in response.content:
        if block.type == "tool_use" and block.name == "extract_layer2":
            return block.input, response
    return None, response


# ──────────────────────────────────────────────
# Batch API (백필용 — 50% 할인 + Prompt Caching)
# ──────────────────────────────────────────────

_BATCH_POLL_INTERVAL = 30  # seconds
_MAX_BATCH_SIZE = 10_000  # Anthropic Batch API 최대 건수


def build_batch_request(custom_id: str, user_content: str) -> Request:
    """Batch API용 개별 요청 생성."""
    return Request(
        custom_id=custom_id,
        params=MessageCreateParamsNonStreaming(
            model=settings.llm_pdf_model,
            max_tokens=8192,
            system=_SYSTEM_PROMPT,
            tools=[_EXTRACT_TOOL_CACHED],
            tool_choice={"type": "tool", "name": "extract_layer2"},
            messages=[{"role": "user", "content": user_content}],
        ),
    )


async def _submit_and_poll_batch(
    requests: list[Request],
) -> tuple[dict[str, tuple[dict | None, int, int, int, int]], list[str]]:
    """단일 배치 제출 + 폴링 + 결과 수집.

    Returns: (results_dict, failed_custom_ids)
    - results_dict: {custom_id: (tool_input, in_tok, out_tok, cc_tok, cr_tok)}
    - failed_custom_ids: errored/expired 엔트리의 custom_id 목록
    """
    client = _get_client()
    _batch_start = time.perf_counter()

    # 제출 retry (3회, 지수 백오프 5~60초)
    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            batch = await client.messages.batches.create(requests=requests)
            break
        except (RateLimitError, APIConnectionError) as e:
            last_exc = e
            wait = min(5 * (2 ** attempt), 60)
            log.warning(
                "layer2_batch_submit_retry",
                attempt=attempt + 1,
                wait_sec=wait,
                error=str(e),
            )
            await asyncio.sleep(wait)
    else:
        raise last_exc  # type: ignore[misc]

    log.info("layer2_batch_submitted", batch_id=batch.id, count=len(requests))
    _save_pending_batch(batch.id, [r["custom_id"] for r in requests])

    # 폴링
    while True:
        batch = await client.messages.batches.retrieve(batch.id)
        if batch.processing_status == "ended":
            break
        log.debug(
            "layer2_batch_polling",
            batch_id=batch.id,
            processing=batch.request_counts.processing,
        )
        await asyncio.sleep(_BATCH_POLL_INTERVAL)

    log.info(
        "layer2_batch_completed",
        batch_id=batch.id,
        succeeded=batch.request_counts.succeeded,
        errored=batch.request_counts.errored,
        expired=batch.request_counts.expired,
        duration_s=round(time.perf_counter() - _batch_start, 2),
    )
    _remove_pending_batch(batch.id)

    # 결과 수집
    results: dict[str, tuple[dict | None, int, int, int, int]] = {}
    failed_ids: list[str] = []
    async for entry in await client.messages.batches.results(batch.id):
        if entry.result.type == "succeeded":
            msg = entry.result.message
            tool_input = None
            for block in msg.content:
                if block.type == "tool_use" and block.name == "extract_layer2":
                    tool_input = block.input
                    break
            usage = msg.usage
            results[entry.custom_id] = (
                tool_input,
                usage.input_tokens,
                usage.output_tokens,
                getattr(usage, "cache_creation_input_tokens", 0) or 0,
                getattr(usage, "cache_read_input_tokens", 0) or 0,
            )
        else:
            log.warning(
                "layer2_batch_entry_failed",
                custom_id=entry.custom_id,
                result_type=entry.result.type,
            )
            failed_ids.append(entry.custom_id)

    return results, failed_ids


async def submit_layer2_batch(requests: list[Request]) -> str:
    """제출만 하고 끝 (fire-and-forget). 폴링/결과수집 없음.

    Returns: batch_id string
    """
    if not requests:
        raise ValueError("requests must not be empty")

    client = _get_client()

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            batch = await client.messages.batches.create(requests=requests)
            break
        except (RateLimitError, APIConnectionError) as e:
            last_exc = e
            wait = min(5 * (2 ** attempt), 60)
            log.warning(
                "layer2_batch_submit_retry",
                attempt=attempt + 1,
                wait_sec=wait,
                error=str(e),
            )
            await asyncio.sleep(wait)
    else:
        raise last_exc  # type: ignore[misc]

    _save_pending_batch(batch.id, [r["custom_id"] for r in requests])
    log.info("layer2_batch_submitted", batch_id=batch.id, count=len(requests))
    return batch.id


async def run_layer2_batch(
    requests: list[Request],
) -> tuple[dict[str, tuple[dict | None, int, int, int, int]], list[str]]:
    """Batch API로 Layer2 추출 실행. 10,000건 초과 시 자동 청크 분할.

    Returns: (results_dict, failed_custom_ids)
    - results_dict: {custom_id: (tool_input, input_tokens, output_tokens,
                     cache_creation_tokens, cache_read_tokens)}
    - failed_custom_ids: errored/expired 엔트리의 custom_id 목록 (pipeline_status='analysis_failed' 설정용)
    """
    if not requests:
        return {}, []

    all_results: dict[str, tuple[dict | None, int, int, int, int]] = {}
    all_failed: list[str] = []

    # 10,000건 초과 시 청크 분할
    for i in range(0, len(requests), _MAX_BATCH_SIZE):
        chunk = requests[i : i + _MAX_BATCH_SIZE]
        if len(requests) > _MAX_BATCH_SIZE:
            log.info(
                "layer2_batch_chunk",
                chunk_start=i,
                chunk_size=len(chunk),
                total=len(requests),
            )
        results, failed = await _submit_and_poll_batch(chunk)
        all_results.update(results)
        all_failed.extend(failed)

    return all_results, all_failed


# ──────────────────────────────────────────────
# 결과 변환
# ──────────────────────────────────────────────

def make_layer2_result(
    tool_input: dict | None,
    input_tokens: int,
    output_tokens: int,
    cache_creation_tokens: int = 0,
    cache_read_tokens: int = 0,
    md_was_truncated: bool = False,
    md_original_chars: int = 0,
    is_batch: bool = False,
) -> Layer2Result | None:
    """LLM 응답 데이터를 Layer2Result로 변환."""
    if tool_input is None:
        return None

    model = settings.llm_pdf_model
    cost = calc_cost_usd(
        model, input_tokens, output_tokens,
        cache_creation_tokens=cache_creation_tokens,
        cache_read_tokens=cache_read_tokens,
        is_batch=is_batch,
    )

    analysis_data = {}
    for key in ("target", "opinion", "thesis", "chain", "financials"):
        if key in tool_input:
            analysis_data[key] = tool_input[key]
    if "meta" in tool_input:
        analysis_data["meta"] = tool_input["meta"]

    try:
        category_confidence = float(tool_input.get("category_confidence", 1.0))
    except (ValueError, TypeError):
        category_confidence = 1.0
    secondary_category = tool_input.get("secondary_category") or None

    analysis_data["category_confidence"] = category_confidence
    if secondary_category is not None:
        analysis_data["secondary_category"] = secondary_category

    quality = tool_input.get("extraction_quality", "medium")
    if md_was_truncated:
        quality = "truncated"

    return Layer2Result(
        report_category=tool_input.get("report_category", "stock"),
        analysis_data=analysis_data,
        meta=tool_input.get("meta", {}),
        stock_mentions=tool_input.get("stock_mentions", []),
        sector_mentions=tool_input.get("sector_mentions", []),
        keywords=tool_input.get("keywords", []),
        extraction_quality=quality,
        category_confidence=category_confidence,
        secondary_category=secondary_category,
        markdown_truncated=md_was_truncated,
        markdown_original_chars=md_original_chars,
        llm_model=model,
        llm_cost_usd=cost,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


# ──────────────────────────────────────────────
# 공개 인터페이스 (실시간용 — listener)
# ──────────────────────────────────────────────

async def extract_layer2(
    text: str,
    markdown: str | None = None,
    chart_texts: list[str] | None = None,
    channel: str = "",
    report_id: int | None = None,
) -> Layer2Result | None:
    """
    Layer 2 구조화 추출 (실시간 — Prompt Caching 적용).

    배치 모드는 build_user_content + build_batch_request + run_layer2_batch 사용.
    """
    if not settings.analysis_enabled or not settings.anthropic_api_key:
        return None

    user_content, md_was_truncated, md_original_chars = build_user_content(
        text, markdown, chart_texts, channel,
    )

    try:
        result, response = await _call_extract(user_content)
    except Exception as e:
        log.warning("layer2_extract_failed", error=str(e), report_id=report_id)
        return None

    usage = response.usage
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0

    await record_llm_usage(
        model=settings.llm_pdf_model,
        purpose="layer2_extract",
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
        source_channel=channel,
        report_id=report_id,
    )

    layer2 = make_layer2_result(
        result, usage.input_tokens, usage.output_tokens,
        cache_creation, cache_read,
        md_was_truncated, md_original_chars,
    )

    if layer2 is None:
        log.warning("layer2_no_result", report_id=report_id)
        return None

    log.info(
        "layer2_extracted",
        report_id=report_id,
        category=layer2.report_category,
        quality=layer2.extraction_quality,
        chain_steps=len(layer2.analysis_data.get("chain", [])),
        stocks=len(layer2.stock_mentions),
    )
    return layer2
