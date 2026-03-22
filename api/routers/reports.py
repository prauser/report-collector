"""리포트 검색/조회 엔드포인트."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from api.layer2_helpers import _display_title, _layer2_summary_from_analysis, _to_summary
from api.schemas import (
    FilterOptions,
    Layer2Data,
    Layer2Keyword,
    Layer2SectorMention,
    Layer2StockMention,
    PaginatedReports,
    ReportDetail,
    ReportSummary,
)
from db.models import Report, ReportAnalysis, ReportKeyword, ReportSectorMention, ReportStockMention

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("", response_model=PaginatedReports)
async def list_reports(
    q: str | None = Query(None, description="제목/종목 검색"),
    stock: str | None = Query(None, description="종목명 또는 코드"),
    broker: str | None = Query(None),
    opinion: str | None = Query(None),
    report_type: str | None = Query(None),
    channel: str | None = Query(None),
    from_date: date | None = Query(None),
    to_date: date | None = Query(None),
    has_ai: bool | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    # Base query on reports only (for filtering/counting)
    base_stmt = select(Report)

    if q:
        base_stmt = base_stmt.where(
            or_(
                Report.title.ilike(f"%{q}%"),
                Report.stock_name.ilike(f"%{q}%"),
            )
        )
    if stock:
        base_stmt = base_stmt.where(
            or_(
                Report.stock_name.ilike(f"%{stock}%"),
                Report.stock_code == stock,
            )
        )
    if broker:
        base_stmt = base_stmt.where(Report.broker == broker)
    if opinion:
        base_stmt = base_stmt.where(Report.opinion == opinion)
    if report_type:
        base_stmt = base_stmt.where(Report.report_type == report_type)
    if channel:
        base_stmt = base_stmt.where(Report.source_channel == channel)
    if from_date:
        base_stmt = base_stmt.where(Report.report_date >= from_date)
    if to_date:
        base_stmt = base_stmt.where(Report.report_date <= to_date)
    if has_ai is True:
        base_stmt = base_stmt.where(Report.ai_processed_at.isnot(None))
    elif has_ai is False:
        base_stmt = base_stmt.where(Report.ai_processed_at.is_(None))

    # 전체 count
    count_stmt = select(func.count()).select_from(base_stmt.subquery())
    total = await db.scalar(count_stmt)

    # 페이지네이션 + LEFT JOIN report_analysis (N+1 방지)
    paged_stmt = (
        base_stmt
        .order_by(Report.report_date.desc(), Report.collected_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    result = await db.execute(paged_stmt)
    reports = result.scalars().all()

    # Batch-fetch analysis for these report IDs
    report_ids = [r.id for r in reports]
    analysis_map: dict[int, ReportAnalysis] = {}
    if report_ids:
        ra_stmt = select(ReportAnalysis).where(ReportAnalysis.report_id.in_(report_ids))
        ra_result = await db.execute(ra_stmt)
        for ra in ra_result.scalars().all():
            analysis_map[ra.report_id] = ra

    return PaginatedReports(
        total=total or 0,
        page=page,
        limit=limit,
        items=[_to_summary(r, analysis_map.get(r.id)) for r in reports],
    )


@router.get("/filters", response_model=FilterOptions)
async def get_filter_options(db: AsyncSession = Depends(get_db)):
    """필터 드롭다운용 유니크 값 목록."""
    brokers = (await db.execute(select(Report.broker).distinct().order_by(Report.broker))).scalars().all()
    opinions = (
        await db.execute(
            select(Report.opinion).distinct().where(Report.opinion.isnot(None)).order_by(Report.opinion)
        )
    ).scalars().all()
    report_types = (
        await db.execute(
            select(Report.report_type).distinct().where(Report.report_type.isnot(None)).order_by(Report.report_type)
        )
    ).scalars().all()
    channels = (
        await db.execute(select(Report.source_channel).distinct().order_by(Report.source_channel))
    ).scalars().all()

    return FilterOptions(
        brokers=list(brokers),
        opinions=list(opinions),
        report_types=list(report_types),
        channels=list(channels),
    )


@router.get("/{report_id}", response_model=ReportDetail)
async def get_report(report_id: int, db: AsyncSession = Depends(get_db)):
    report = await db.get(Report, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    # Load analysis with related tables
    ra_result = await db.execute(
        select(ReportAnalysis).where(ReportAnalysis.report_id == report_id)
    )
    ra: ReportAnalysis | None = ra_result.scalar_one_or_none()

    layer2: Layer2Data | None = None
    if ra is not None:
        # Load stock mentions
        sm_result = await db.execute(
            select(ReportStockMention).where(ReportStockMention.report_id == report_id)
        )
        stock_mentions = [
            Layer2StockMention(
                stock_code=sm.stock_code,
                company_name=sm.company_name,
                mention_type=sm.mention_type,
                impact=sm.impact,
                relevance_score=float(sm.relevance_score) if sm.relevance_score is not None else None,
            )
            for sm in sm_result.scalars().all()
        ]

        # Load sector mentions
        sect_result = await db.execute(
            select(ReportSectorMention).where(ReportSectorMention.report_id == report_id)
        )
        sector_mentions = [
            Layer2SectorMention(
                sector=sect.sector,
                mention_type=sect.mention_type,
                impact=sect.impact,
            )
            for sect in sect_result.scalars().all()
        ]

        # Load keywords
        kw_result = await db.execute(
            select(ReportKeyword).where(ReportKeyword.report_id == report_id)
        )
        keywords = [
            Layer2Keyword(
                keyword=kw.keyword,
                keyword_type=kw.keyword_type,
            )
            for kw in kw_result.scalars().all()
        ]

        layer2 = Layer2Data(
            report_category=ra.report_category,
            analysis_data=ra.analysis_data,
            extraction_quality=ra.extraction_quality,
            stock_mentions=stock_mentions,
            sector_mentions=sector_mentions,
            keywords=keywords,
        )

    return ReportDetail(
        **_to_summary(report, ra).model_dump(),
        ai_summary=report.ai_summary,
        ai_keywords=report.ai_keywords,
        ai_processed_at=report.ai_processed_at,
        pdf_url=report.pdf_url,
        pdf_path=report.pdf_path,
        pdf_size_kb=report.pdf_size_kb,
        page_count=report.page_count,
        earnings_quarter=report.earnings_quarter,
        est_revenue=report.est_revenue,
        est_op_profit=report.est_op_profit,
        est_eps=report.est_eps,
        raw_text=report.raw_text,
        source_message_id=report.source_message_id,
        layer2=layer2,
    )
