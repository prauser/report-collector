"""통계/대시보드 엔드포인트."""
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import cast, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Date

from api.deps import get_db
from api.schemas import LlmStats, LlmUsageStat, OverviewStats
from db.models import BackfillRun, Channel, LlmUsage, Report, ReportAnalysis

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

    # Layer 2 분석 상태
    analysis_done = await db.scalar(
        select(func.count(Report.id)).where(Report.analysis_status == "done")
    ) or 0
    analysis_failed = await db.scalar(
        select(func.count(Report.id)).where(Report.analysis_status == "failed")
    ) or 0
    analysis_truncated = await db.scalar(
        select(func.count(Report.id)).where(Report.analysis_status == "truncated")
    ) or 0
    analysis_pending = (total or 0) - analysis_done - analysis_failed - analysis_truncated

    # Layer 2 카테고리 분포
    cat_rows = (
        await db.execute(
            select(ReportAnalysis.report_category, func.count(ReportAnalysis.id))
            .group_by(ReportAnalysis.report_category)
            .order_by(func.count(ReportAnalysis.id).desc())
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
        analysis_done=analysis_done,
        analysis_pending=analysis_pending,
        analysis_failed=analysis_failed,
        analysis_truncated=analysis_truncated,
        analysis_by_category=[{"category": r[0], "count": r[1]} for r in cat_rows],
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


@router.get("/backfill")
async def get_backfill_stats(db: AsyncSession = Depends(get_db)):
    """채널별 백필 현황 + PDF/AI 분석 커버리지."""

    # 활성 채널 목롮 (기준)
    active_channels = (await db.scalars(
        select(Channel.channel_username).where(Channel.is_active == True)
    )).all()

    # 채널별 백필 런 누적 통계
    run_rows = (
        await db.execute(
            select(
                BackfillRun.channel_username,
                func.max(BackfillRun.run_date).label("last_run_date"),
                func.max(BackfillRun.finished_at).label("last_finished_at"),
                func.max(BackfillRun.to_message_id).label("latest_message_id"),
                func.min(BackfillRun.from_message_id).label("earliest_from_id"),
                func.count(BackfillRun.id).label("total_runs"),
                func.sum(BackfillRun.n_scanned).label("total_scanned"),
                func.sum(BackfillRun.n_saved).label("total_saved"),
                func.sum(BackfillRun.n_pending).label("total_pending"),
                func.sum(BackfillRun.n_skipped).label("total_skipped"),
            )
            .group_by(BackfillRun.channel_username)
        )
    ).all()
    run_map = {r.channel_username: r for r in run_rows}

    # 채널별 런 히스토리 (최근 20건)
    history_rows = (
        await db.execute(
            select(
                BackfillRun.channel_username,
                BackfillRun.run_date,
                BackfillRun.started_at,
                BackfillRun.finished_at,
                BackfillRun.from_message_id,
                BackfillRun.to_message_id,
                BackfillRun.n_scanned,
                BackfillRun.n_saved,
                BackfillRun.n_pending,
                BackfillRun.n_skipped,
                BackfillRun.status,
                BackfillRun.error_msg,
            )
            .order_by(BackfillRun.started_at.desc())
            .limit(20)
        )
    ).all()

    # 채널별 PDF / AI 커버리지
    coverage_rows = (
        await db.execute(
            select(
                Report.source_channel,
                func.count(Report.id).label("total"),
                func.count(Report.pdf_url).label("has_pdf_url"),
                func.count(Report.pdf_path).label("pdf_downloaded"),
                func.count(Report.ai_processed_at).label("ai_analyzed"),
            )
            .group_by(Report.source_channel)
            .order_by(Report.source_channel)
        )
    ).all()

    # parse_quality 분포
    quality_rows = (
        await db.execute(
            select(
                Report.source_channel,
                Report.parse_quality,
                func.count(Report.id).label("cnt"),
            )
            .group_by(Report.source_channel, Report.parse_quality)
            .order_by(Report.source_channel)
        )
    ).all()

    # 채널별로 그룹핑
    quality_map: dict[str, dict[str, int]] = {}
    for r in quality_rows:
        ch = r.source_channel
        q = r.parse_quality or "unknown"
        quality_map.setdefault(ch, {"good": 0, "partial": 0, "poor": 0, "unknown": 0})
        quality_map[ch][q] = r.cnt

    # pdf_download_failed 건수 (채널별)
    pdf_failed_rows = (
        await db.execute(
            select(
                Report.source_channel,
                func.count(Report.id).label("cnt"),
            )
            .where(Report.pdf_download_failed == True)
            .group_by(Report.source_channel)
        )
    ).all()
    pdf_failed_map = {r.source_channel: r.cnt for r in pdf_failed_rows}

    return {
        "by_channel": [
            {
                "channel": ch,
                "last_run_date": str(run_map[ch].last_run_date) if ch in run_map and run_map[ch].last_run_date else None,
                "last_finished_at": run_map[ch].last_finished_at.isoformat() if ch in run_map and run_map[ch].last_finished_at else None,
                "latest_message_id": run_map[ch].latest_message_id if ch in run_map else None,
                "earliest_from_id": run_map[ch].earliest_from_id if ch in run_map else None,
                "total_runs": run_map[ch].total_runs if ch in run_map else 0,
                "total_scanned": run_map[ch].total_scanned or 0 if ch in run_map else 0,
                "total_saved": run_map[ch].total_saved or 0 if ch in run_map else 0,
                "total_pending": run_map[ch].total_pending or 0 if ch in run_map else 0,
                "total_skipped": run_map[ch].total_skipped or 0 if ch in run_map else 0,
            }
            for ch in sorted(active_channels)
        ],
        "recent_runs": [
            {
                "channel": r.channel_username,
                "run_date": str(r.run_date),
                "started_at": r.started_at.isoformat(),
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "from_message_id": r.from_message_id,
                "to_message_id": r.to_message_id,
                "n_scanned": r.n_scanned,
                "n_saved": r.n_saved,
                "n_pending": r.n_pending,
                "n_skipped": r.n_skipped,
                "status": r.status,
                "error_msg": r.error_msg,
            }
            for r in history_rows
        ],
        "pdf_coverage": [
            {
                "channel": r.source_channel,
                "total_reports": r.total,
                "has_pdf_url": r.has_pdf_url,
                "pdf_downloaded": r.pdf_downloaded,
                "ai_analyzed": r.ai_analyzed,
                "pdf_failed": pdf_failed_map.get(r.source_channel, 0),
                "parse_quality": quality_map.get(r.source_channel, {"good": 0, "partial": 0, "poor": 0, "unknown": 0}),
            }
            for r in coverage_rows
        ],
    }
