"""Layer 2 분석 결과 트랜잭션 저장."""
from __future__ import annotations

from datetime import datetime, timezone

import structlog
from sqlalchemy import delete, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import settings
from db.models import (
    AnalysisJob,
    Report,
    ReportAnalysis,
    ReportKeyword,
    ReportMarkdown,
    ReportSectorMention,
    ReportStockMention,
)
from parser.layer2_extractor import Layer2Result
from parser.markdown_converter import _estimate_token_count

log = structlog.get_logger(__name__)


async def save_markdown(
    session: AsyncSession,
    report_id: int,
    markdown_text: str,
    converter: str,
) -> None:
    """Markdown 변환 결과를 저장 (upsert)."""
    stmt = insert(ReportMarkdown).values(
        report_id=report_id,
        markdown_text=markdown_text,
        converter=converter,
        token_count=_estimate_token_count(markdown_text),
        created_at=datetime.now(timezone.utc),
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_report_markdown",
        set_={
            "markdown_text": stmt.excluded.markdown_text,
            "converter": stmt.excluded.converter,
            "token_count": stmt.excluded.token_count,
            "updated_at": datetime.now(timezone.utc),
        },
    )
    await session.execute(stmt)

    await session.execute(
        update(Report)
        .where(Report.id == report_id)
        .values(markdown_converted=True)
    )


async def save_analysis(
    session: AsyncSession,
    report_id: int,
    layer2: Layer2Result,
) -> None:
    """
    Layer 2 분석 결과를 단일 트랜잭션으로 저장.

    1. report_analysis INSERT (or UPDATE)
    2. report_stock_mentions DELETE + INSERT
    3. report_sector_mentions DELETE + INSERT
    4. report_keywords DELETE + INSERT
    5. reports.analysis_status = 'done' UPDATE
    6. analysis_jobs 로그 기록
    """
    now = datetime.now(timezone.utc)

    # 1. report_analysis upsert
    stmt = insert(ReportAnalysis).values(
        report_id=report_id,
        report_category=layer2.report_category,
        analysis_data=layer2.analysis_data,
        llm_model=layer2.llm_model,
        llm_cost_usd=layer2.llm_cost_usd,
        schema_version=settings.analysis_schema_version,
        extraction_quality=layer2.extraction_quality,
        created_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_report_analysis",
        set_={
            "report_category": stmt.excluded.report_category,
            "analysis_data": stmt.excluded.analysis_data,
            "llm_model": stmt.excluded.llm_model,
            "llm_cost_usd": stmt.excluded.llm_cost_usd,
            "schema_version": stmt.excluded.schema_version,
            "extraction_quality": stmt.excluded.extraction_quality,
            "updated_at": now,
        },
    )
    await session.execute(stmt)

    # 2. stock_mentions: DELETE existing + INSERT new
    await session.execute(
        delete(ReportStockMention).where(ReportStockMention.report_id == report_id)
    )
    if layer2.stock_mentions:
        seen_codes = set()
        stock_rows = []
        for sm in layer2.stock_mentions:
            code = sm.get("stock_code") or ""
            name = sm.get("company_name") or ""
            if not code and not name:
                continue
            # stock_code가 빈 문자열이면 company_name 기반 임시 키 사용
            dedup_key = code if code else f"_name_{name}"
            if dedup_key in seen_codes:
                continue
            seen_codes.add(dedup_key)
            stock_rows.append({
                "report_id": report_id,
                "stock_code": code or name[:20],  # 빈 코드 시 이름으로 대체
                "company_name": name or None,
                "mention_type": sm.get("mention_type", "related"),
                "impact": sm.get("impact"),
                "relevance_score": sm.get("relevance_score"),
            })
        if stock_rows:
            await session.execute(insert(ReportStockMention), stock_rows)

    # 3. sector_mentions: DELETE existing + INSERT new
    await session.execute(
        delete(ReportSectorMention).where(ReportSectorMention.report_id == report_id)
    )
    if layer2.sector_mentions:
        sector_rows = [
            {
                "report_id": report_id,
                "sector": sm.get("sector", ""),
                "mention_type": sm.get("mention_type", "primary"),
                "impact": sm.get("impact"),
            }
            for sm in layer2.sector_mentions
            if sm.get("sector")
        ]
        if sector_rows:
            await session.execute(insert(ReportSectorMention), sector_rows)

    # 4. keywords: DELETE existing + INSERT new
    await session.execute(
        delete(ReportKeyword).where(ReportKeyword.report_id == report_id)
    )
    if layer2.keywords:
        kw_rows = [
            {
                "report_id": report_id,
                "keyword": kw.get("keyword", ""),
                "keyword_type": kw.get("keyword_type"),
            }
            for kw in layer2.keywords
            if kw.get("keyword")
        ]
        if kw_rows:
            await session.execute(insert(ReportKeyword), kw_rows)

    # 5. reports.analysis_status 업데이트
    status = "truncated" if layer2.markdown_truncated else "done"
    await session.execute(
        update(Report)
        .where(Report.id == report_id)
        .values(
            analysis_status=status,
            analysis_version=settings.analysis_schema_version,
        )
    )

    # 6. analysis_jobs 로그
    session.add(AnalysisJob(
        report_id=report_id,
        job_type="extract_layer2",
        status="success",
        llm_model=layer2.llm_model,
        input_tokens=layer2.input_tokens,
        output_tokens=layer2.output_tokens,
        cost_usd=layer2.llm_cost_usd,
        target_schema_version=settings.analysis_schema_version,
        started_at=now,
        finished_at=now,
    ))

    log.info(
        "analysis_saved",
        report_id=report_id,
        category=layer2.report_category,
        quality=layer2.extraction_quality,
        status=status,
        **({"original_chars": layer2.markdown_original_chars} if layer2.markdown_truncated else {}),
    )


async def log_analysis_failure(
    session: AsyncSession,
    report_id: int,
    job_type: str,
    error_message: str,
) -> None:
    """분석 실패 로그 기록."""
    now = datetime.now(timezone.utc)
    session.add(AnalysisJob(
        report_id=report_id,
        job_type=job_type,
        status="failed",
        error_message=error_message[:500],
        started_at=now,
        finished_at=now,
    ))
    await session.execute(
        update(Report)
        .where(Report.id == report_id)
        .values(analysis_status="failed")
    )
