"""리포트 검색/조회 엔드포인트."""
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from api.schemas import FilterOptions, PaginatedReports, ReportDetail, ReportSummary
from db.models import Report

router = APIRouter(prefix="/reports", tags=["reports"])


def _to_summary(r: Report) -> ReportSummary:
    return ReportSummary(
        id=r.id,
        broker=r.broker,
        report_date=r.report_date,
        analyst=r.analyst,
        stock_name=r.stock_name,
        stock_code=r.stock_code,
        title=r.title,
        sector=r.sector,
        report_type=r.report_type,
        opinion=r.opinion,
        target_price=r.target_price,
        prev_opinion=r.prev_opinion,
        prev_target_price=r.prev_target_price,
        has_pdf=r.pdf_path is not None,
        has_ai=r.ai_processed_at is not None,
        ai_sentiment=r.ai_sentiment,
        collected_at=r.collected_at,
        source_channel=r.source_channel,
    )


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
    stmt = select(Report)

    if q:
        stmt = stmt.where(
            or_(
                Report.title.ilike(f"%{q}%"),
                Report.stock_name.ilike(f"%{q}%"),
            )
        )
    if stock:
        stmt = stmt.where(
            or_(
                Report.stock_name.ilike(f"%{stock}%"),
                Report.stock_code == stock,
            )
        )
    if broker:
        stmt = stmt.where(Report.broker == broker)
    if opinion:
        stmt = stmt.where(Report.opinion == opinion)
    if report_type:
        stmt = stmt.where(Report.report_type == report_type)
    if channel:
        stmt = stmt.where(Report.source_channel == channel)
    if from_date:
        stmt = stmt.where(Report.report_date >= from_date)
    if to_date:
        stmt = stmt.where(Report.report_date <= to_date)
    if has_ai is True:
        stmt = stmt.where(Report.ai_processed_at.isnot(None))
    elif has_ai is False:
        stmt = stmt.where(Report.ai_processed_at.is_(None))

    # 전체 count
    count_stmt = select(func.count()).select_from(stmt.subquery())
    total = await db.scalar(count_stmt)

    # 페이지네이션
    stmt = stmt.order_by(Report.report_date.desc(), Report.collected_at.desc())
    stmt = stmt.offset((page - 1) * limit).limit(limit)

    result = await db.execute(stmt)
    reports = result.scalars().all()

    return PaginatedReports(
        total=total or 0,
        page=page,
        limit=limit,
        items=[_to_summary(r) for r in reports],
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

    return ReportDetail(
        **_to_summary(report).model_dump(),
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
    )
