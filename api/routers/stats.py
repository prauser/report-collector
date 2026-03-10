"""통계/대시보드 엔드포인트."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Date

from api.deps import get_db
from api.schemas import LlmStats, LlmUsageStat, OverviewStats
from db.models import LlmUsage, Report

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/overview", response_model=OverviewStats)
async def get_overview(db: AsyncSession = Depends(get_db)):
    today = date.today()

    total = await db.scalar(select(func.count(Report.id)))
    today_count = await db.scalar(
        select(func.count(Report.id)).where(Report.report_date == today)
    )
    pdf_count = await db.scalar(
        select(func.count(Report.id)).where(Report.pdf_path.isnot(None))
    )
    ai_count = await db.scalar(
        select(func.count(Report.id)).where(Report.ai_processed_at.isnot(None))
    )

    # 상위 10개 증권사
    broker_rows = (
        await db.execute(
            select(Report.broker, func.count(Report.id).label("cnt"))
            .group_by(Report.broker)
            .order_by(func.count(Report.id).desc())
            .limit(10)
        )
    ).all()

    # 상위 10개 종목 (최근 30일)
    stock_rows = (
        await db.execute(
            select(Report.stock_name, func.count(Report.id).label("cnt"))
            .where(Report.stock_name.isnot(None))
            .where(Report.report_date >= today - timedelta(days=30))
            .group_by(Report.stock_name)
            .order_by(func.count(Report.id).desc())
            .limit(10)
        )
    ).all()

    return OverviewStats(
        total_reports=total or 0,
        reports_today=today_count or 0,
        reports_with_pdf=pdf_count or 0,
        reports_with_ai=ai_count or 0,
        top_brokers=[{"broker": r[0], "count": r[1]} for r in broker_rows],
        top_stocks=[{"stock": r[0], "count": r[1]} for r in stock_rows],
    )


@router.get("/llm", response_model=LlmStats)
async def get_llm_stats(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    since = date.today() - timedelta(days=days)

    base = select(LlmUsage).where(cast(LlmUsage.called_at, Date) >= since)

    # 전체 비용
    total_cost = await db.scalar(
        select(func.sum(LlmUsage.cost_usd)).where(cast(LlmUsage.called_at, Date) >= since)
    )

    # purpose별 집계
    purpose_rows = (
        await db.execute(
            select(
                LlmUsage.model,
                LlmUsage.purpose,
                LlmUsage.message_type,
                func.count(LlmUsage.id).label("cnt"),
                func.sum(LlmUsage.input_tokens).label("in_tok"),
                func.sum(LlmUsage.output_tokens).label("out_tok"),
                func.sum(LlmUsage.cost_usd).label("cost"),
            )
            .where(cast(LlmUsage.called_at, Date) >= since)
            .group_by(LlmUsage.model, LlmUsage.purpose, LlmUsage.message_type)
            .order_by(func.sum(LlmUsage.cost_usd).desc())
        )
    ).all()

    # message_type별 집계 (필터링 비율)
    msg_type_rows = (
        await db.execute(
            select(
                LlmUsage.message_type,
                func.count(LlmUsage.id).label("cnt"),
                func.sum(LlmUsage.cost_usd).label("cost"),
            )
            .where(cast(LlmUsage.called_at, Date) >= since)
            .where(LlmUsage.purpose == "parse")
            .group_by(LlmUsage.message_type)
            .order_by(func.count(LlmUsage.id).desc())
        )
    ).all()

    # 일별 비용 (최근 N일)
    daily_rows = (
        await db.execute(
            select(
                cast(LlmUsage.called_at, Date).label("day"),
                func.sum(LlmUsage.cost_usd).label("cost"),
            )
            .where(cast(LlmUsage.called_at, Date) >= since)
            .group_by(cast(LlmUsage.called_at, Date))
            .order_by(cast(LlmUsage.called_at, Date))
        )
    ).all()

    return LlmStats(
        period_days=days,
        total_cost_usd=total_cost or 0,
        by_purpose=[
            LlmUsageStat(
                model=r[0],
                purpose=r[1],
                message_type=r[2],
                call_count=r[3],
                total_input_tokens=r[4],
                total_output_tokens=r[5],
                total_cost_usd=r[6],
            )
            for r in purpose_rows
        ],
        by_message_type=[
            {"message_type": r[0] or "unknown", "count": r[1], "cost_usd": float(r[2] or 0)}
            for r in msg_type_rows
        ],
        daily_cost=[
            {"date": str(r[0]), "cost_usd": float(r[1] or 0)} for r in daily_rows
        ],
    )
