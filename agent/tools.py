"""Anthropic tool-use API 형식의 DB 검색 도구 4개.

AGENT_TOOLS: Anthropic API에 전달할 tool 스키마 리스트
execute_tool: tool name + input dict + AsyncSession → dict dispatcher
"""
from __future__ import annotations

import asyncio
import datetime
import json
from typing import Any

import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import Report, ReportAnalysis

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Tool 스키마 (Anthropic tool-use format)
# ---------------------------------------------------------------------------

AGENT_TOOLS: list[dict] = [
    {
        "name": "search_reports",
        "description": (
            "리포트를 검색합니다. 종목명(부분일치), 종목코드(정확일치), 섹터(부분일치), "
            "증권사(부분일치), 날짜 범위로 필터링할 수 있습니다. "
            "분석 데이터가 없는 리포트도 결과에 포함됩니다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "stock_name": {
                    "type": "string",
                    "description": "종목명 (부분일치, 예: '삼성'으로 '삼성전자' 검색 가능)",
                },
                "stock_code": {
                    "type": "string",
                    "description": "종목코드 6자리 (정확일치, 예: '005930')",
                },
                "sector": {
                    "type": "string",
                    "description": "섹터/업종 (부분일치, 예: '반도체')",
                },
                "broker": {
                    "type": "string",
                    "description": "증권사명 (부분일치, 예: '미래에셋')",
                },
                "date_from": {
                    "type": "string",
                    "description": "시작 날짜 (YYYY-MM-DD 형식)",
                },
                "date_to": {
                    "type": "string",
                    "description": "종료 날짜 (YYYY-MM-DD 형식)",
                },
                "limit": {
                    "type": "integer",
                    "description": "반환할 최대 건수 (기본 20, 최대 50)",
                    "default": 20,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_report_detail",
        "description": (
            "특정 리포트 ID 목록의 상세 정보를 조회합니다. "
            "분석 데이터(JSONB)가 있는 리포트만 반환됩니다. "
            "report_id는 search_reports 결과에서 얻을 수 있습니다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "report_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "조회할 리포트 ID 목록 (최대 10개)",
                },
            },
            "required": ["report_ids"],
        },
    },
    {
        "name": "list_stocks",
        "description": (
            "리포트가 존재하는 종목 목록을 조회합니다. "
            "종목명 검색, 섹터 필터, 정렬 옵션을 제공합니다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "search": {
                    "type": "string",
                    "description": "종목명 검색어 (부분일치)",
                },
                "sector": {
                    "type": "string",
                    "description": "섹터/업종 필터 (부분일치)",
                },
                "sort": {
                    "type": "string",
                    "enum": ["report_count", "latest_date"],
                    "description": "정렬 기준: report_count(리포트 수 내림차순), latest_date(최신 리포트 날짜 내림차순)",
                    "default": "report_count",
                },
                "limit": {
                    "type": "integer",
                    "description": "반환할 최대 건수 (기본 20, 최대 50)",
                    "default": 20,
                },
            },
            "required": [],
        },
    },
    {
        "name": "get_report_stats",
        "description": (
            "기간별 리포트 집계 통계를 조회합니다. "
            "증권사별/섹터별 리포트 수, 많이 다뤄진 종목 순위를 제공합니다."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "date_from": {
                    "type": "string",
                    "description": "시작 날짜 (YYYY-MM-DD 형식, 기본: 오늘로부터 30일 전)",
                },
                "date_to": {
                    "type": "string",
                    "description": "종료 날짜 (YYYY-MM-DD 형식, 기본: 오늘)",
                },
            },
            "required": [],
        },
    },
]

# ---------------------------------------------------------------------------
# Executor 함수
# ---------------------------------------------------------------------------


async def execute_search_reports(input: dict, session: AsyncSession) -> dict:
    """Report 검색 — 분석 없는 리포트도 포함."""
    stock_name: str | None = input.get("stock_name")
    stock_code: str | None = input.get("stock_code")
    sector: str | None = input.get("sector")
    broker: str | None = input.get("broker")
    date_from: str | None = input.get("date_from")
    date_to: str | None = input.get("date_to")
    limit: int = max(1, min(int(input.get("limit", 20)), 50))

    if date_from:
        try:
            date_from_val = datetime.date.fromisoformat(date_from)
        except ValueError:
            return {"error": "잘못된 날짜 형식입니다. YYYY-MM-DD 형식을 사용하세요."}
    else:
        date_from_val = None

    if date_to:
        try:
            date_to_val = datetime.date.fromisoformat(date_to)
        except ValueError:
            return {"error": "잘못된 날짜 형식입니다. YYYY-MM-DD 형식을 사용하세요."}
    else:
        date_to_val = None

    stmt = select(
        Report.id,
        Report.broker,
        Report.report_date,
        Report.title,
        Report.stock_name,
        Report.stock_code,
        Report.sector,
        Report.opinion,
        Report.target_price,
    )

    if stock_name:
        stmt = stmt.where(Report.stock_name.ilike(f"%{stock_name}%"))
    if stock_code:
        stmt = stmt.where(Report.stock_code == stock_code)
    if sector:
        stmt = stmt.where(Report.sector.ilike(f"%{sector}%"))
    if broker:
        stmt = stmt.where(Report.broker.ilike(f"%{broker}%"))
    if date_from_val:
        stmt = stmt.where(Report.report_date >= date_from_val)
    if date_to_val:
        stmt = stmt.where(Report.report_date <= date_to_val)

    stmt = stmt.order_by(Report.report_date.desc()).limit(limit)

    result = await session.execute(stmt)
    rows = result.all()

    reports = [
        {
            "report_id": row.id,
            "broker": row.broker,
            "date": str(row.report_date),
            "title": row.title,
            "stock_name": row.stock_name,
            "stock_code": row.stock_code,
            "sector": row.sector,
            "opinion": row.opinion,
            "target_price": row.target_price,
        }
        for row in rows
    ]

    log.debug("execute_search_reports", result_count=len(reports), filters=input)
    return {"reports": reports, "total_count": len(reports)}


async def execute_get_report_detail(input: dict, session: AsyncSession) -> dict:
    """Report INNER JOIN ReportAnalysis — analysis 있는 것만."""
    report_ids: list[int] = input.get("report_ids", [])
    report_ids = report_ids[:10]  # max 10

    if not report_ids:
        return {"reports": []}

    stmt = (
        select(Report, ReportAnalysis)
        .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
        .where(Report.id.in_(report_ids))
        .order_by(Report.report_date.desc())
    )

    result = await session.execute(stmt)
    rows = result.all()

    def _safe_analysis_data(data: Any) -> Any:
        try:
            return json.loads(json.dumps(data, default=str))
        except Exception:
            return None

    reports = [
        {
            "report_id": report.id,
            "broker": report.broker,
            "date": str(report.report_date),
            "title": report.title,
            "stock_name": report.stock_name,
            "stock_code": report.stock_code,
            "sector": report.sector,
            "opinion": report.opinion,
            "target_price": report.target_price,
            "analysis_data": _safe_analysis_data(analysis.analysis_data),
        }
        for report, analysis in rows
    ]

    log.debug("execute_get_report_detail", result_count=len(reports), report_ids=report_ids)
    return {"reports": reports}


async def execute_list_stocks(input: dict, session: AsyncSession) -> dict:
    """Report GROUP BY stock_name, stock_code — 종목 목록 + 리포트 수."""
    search: str | None = input.get("search")
    sector: str | None = input.get("sector")
    sort: str = input.get("sort", "report_count")
    limit: int = max(1, min(int(input.get("limit", 20)), 50))

    # Base subquery conditions (applied to both count and data queries)
    base_where = [Report.stock_name.isnot(None)]
    if search:
        base_where.append(Report.stock_name.ilike(f"%{search}%"))
    if sector:
        base_where.append(Report.sector.ilike(f"%{sector}%"))

    # Count query: distinct (stock_name, stock_code, sector) groups
    count_subq = (
        select(func.count())
        .select_from(
            select(Report.stock_name, Report.stock_code, Report.sector)
            .where(*base_where)
            .group_by(Report.stock_name, Report.stock_code, Report.sector)
            .subquery()
        )
    )

    # Data query with limit
    stmt = (
        select(
            Report.stock_name,
            Report.stock_code,
            Report.sector,
            func.count(Report.id).label("report_count"),
            func.max(Report.report_date).label("latest_report_date"),
        )
        .where(*base_where)
        .group_by(Report.stock_name, Report.stock_code, Report.sector)
    )

    if sort == "latest_date":
        stmt = stmt.order_by(text("latest_report_date DESC"))
    else:
        stmt = stmt.order_by(text("report_count DESC"))

    stmt = stmt.limit(limit)

    result, count_result = await asyncio.gather(
        session.execute(stmt),
        session.execute(count_subq),
    )
    rows = result.all()
    total_count: int = count_result.scalar() or 0

    stocks = [
        {
            "stock_name": row.stock_name,
            "stock_code": row.stock_code,
            "sector": row.sector,
            "report_count": row.report_count,
            "latest_report_date": str(row.latest_report_date) if row.latest_report_date else None,
        }
        for row in rows
    ]

    log.debug("execute_list_stocks", result_count=len(stocks), filters=input)
    return {"stocks": stocks, "total_count": total_count}


async def execute_get_report_stats(input: dict, session: AsyncSession) -> dict:
    """기간별 리포트 통계 집계."""
    today = datetime.date.today()
    date_from_str: str | None = input.get("date_from")
    date_to_str: str | None = input.get("date_to")

    if date_from_str:
        try:
            date_from: datetime.date = datetime.date.fromisoformat(date_from_str)
        except ValueError:
            return {"error": "잘못된 날짜 형식입니다. YYYY-MM-DD 형식을 사용하세요."}
    else:
        date_from = today - datetime.timedelta(days=30)

    if date_to_str:
        try:
            date_to: datetime.date = datetime.date.fromisoformat(date_to_str)
        except ValueError:
            return {"error": "잘못된 날짜 형식입니다. YYYY-MM-DD 형식을 사용하세요."}
    else:
        date_to = today

    base_filter = (
        Report.report_date >= date_from,
        Report.report_date <= date_to,
    )

    total_q = select(func.count(Report.id)).where(*base_filter)
    broker_q = (
        select(Report.broker, func.count(Report.id).label("count"))
        .where(*base_filter)
        .group_by(Report.broker)
        .order_by(func.count(Report.id).desc())
        .limit(20)
    )
    sector_q = (
        select(Report.sector, func.count(Report.id).label("count"))
        .where(Report.sector.isnot(None), *base_filter)
        .group_by(Report.sector)
        .order_by(func.count(Report.id).desc())
        .limit(20)
    )
    stock_q = (
        select(
            Report.stock_name,
            Report.stock_code,
            func.count(Report.id).label("count"),
        )
        .where(Report.stock_name.isnot(None), *base_filter)
        .group_by(Report.stock_name, Report.stock_code)
        .order_by(func.count(Report.id).desc())
        .limit(20)
    )

    total_result, broker_result, sector_result, stock_result = await asyncio.gather(
        session.execute(total_q),
        session.execute(broker_q),
        session.execute(sector_q),
        session.execute(stock_q),
    )

    total_reports: int = total_result.scalar() or 0
    by_broker = [
        {"broker": row.broker, "count": row.count}
        for row in broker_result.all()
    ]
    by_sector = [
        {"sector": row.sector, "count": row.count}
        for row in sector_result.all()
    ]
    top_stocks = [
        {"stock_name": row.stock_name, "stock_code": row.stock_code, "count": row.count}
        for row in stock_result.all()
    ]

    log.debug(
        "execute_get_report_stats",
        date_from=str(date_from),
        date_to=str(date_to),
        total_reports=total_reports,
    )
    return {
        "period": {"from": str(date_from), "to": str(date_to)},
        "total_reports": total_reports,
        "by_broker": by_broker,
        "by_sector": by_sector,
        "top_stocks": top_stocks,
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_EXECUTORS: dict[str, Any] = {
    "search_reports": execute_search_reports,
    "get_report_detail": execute_get_report_detail,
    "list_stocks": execute_list_stocks,
    "get_report_stats": execute_get_report_stats,
}


async def execute_tool(name: str, input: dict, session: AsyncSession) -> dict:
    """Tool name + input + session → 결과 dict.

    알 수 없는 tool name이면 error key를 포함한 dict 반환.
    """
    executor = _EXECUTORS.get(name)
    if executor is None:
        log.warning("execute_tool: unknown tool", tool_name=name)
        return {"error": f"Unknown tool: {name}"}

    log.debug("execute_tool", tool_name=name)
    return await executor(input, session)
