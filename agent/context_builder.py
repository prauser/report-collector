"""컨텍스트 빌더 — 사용자 질문에서 종목/키워드 추출, 관련 리포트 검색."""
from __future__ import annotations

import datetime
import re
import structlog
import yaml
from sqlalchemy import select, or_, text
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    Report,
    ReportAnalysis,
    ReportStockMention,
    ReportSectorMention,
    ReportKeyword,
    StockCode,
)

log = structlog.get_logger(__name__)

# 컨텍스트 예산: 최소 15건, 최대 25건
CONTEXT_BUDGET_MIN = 15
CONTEXT_BUDGET_MAX = 25


async def extract_query_entities(question: str, session: AsyncSession) -> dict:
    """사용자 질문에서 종목명/코드 매칭.

    Returns:
        {
            "stock_codes": ["005930", ...],
            "stock_names": ["삼성전자", ...],
            "keywords": ["반도체", ...],
        }
    """
    stock_codes: list[str] = []
    stock_names: list[str] = []

    # 6자리 숫자 종목코드 직접 추출
    code_pattern = re.compile(r'\b(\d{6})\b')
    raw_codes = code_pattern.findall(question)

    # 종목코드 DB 조회 (실존 종목만)
    if raw_codes:
        result = await session.execute(
            select(StockCode).where(StockCode.code.in_(raw_codes))
        )
        matched = result.scalars().all()
        for sc in matched:
            stock_codes.append(sc.code)
            stock_names.append(sc.name)

    # 종목명 매칭: stock_codes 테이블에서 이름 기반 검색
    # 질문 토큰화 (공백/구두점 기준 분리, 2자 이상)
    tokens = re.split(r'[\s,\.!?\(\)\[\]]+', question)
    tokens = [t for t in tokens if len(t) >= 2]

    if tokens:
        # 이미 매칭된 코드는 제외
        already_matched = set(stock_codes)
        # IN 쿼리로 일괄 조회 (name 또는 name_normalized)
        result = await session.execute(
            select(StockCode).where(
                or_(
                    StockCode.name.in_(tokens),
                    StockCode.name_normalized.in_(tokens),
                )
            )
        )
        for sc in result.scalars().all():
            if sc.code not in already_matched:
                stock_codes.append(sc.code)
                stock_names.append(sc.name)
                already_matched.add(sc.code)

    # 키워드: 섹터/테마 관련 단어 추출 (간단 휴리스틱)
    sector_keywords = _extract_sector_keywords(question, tokens)

    log.debug(
        "extract_query_entities",
        stock_codes=stock_codes,
        stock_names=stock_names,
        keywords=sector_keywords,
    )
    return {
        "stock_codes": stock_codes,
        "stock_names": stock_names,
        "keywords": sector_keywords,
    }


def _extract_sector_keywords(question: str, tokens: list[str]) -> list[str]:
    """간단한 섹터/키워드 추출 (길이 2 이상 한글 토큰)."""
    # 한글 토큰만 (종목코드 제외)
    korean_pattern = re.compile(r'^[가-힣]+$')
    keywords = [t for t in tokens if korean_pattern.match(t) and len(t) >= 2]
    # 중복 제거
    seen = set()
    result = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            result.append(kw)
    return result[:20]  # 최대 20개


async def _find_report_ids_by_entities(
    entities: dict,
    session: AsyncSession,
) -> dict[int, float]:
    """엔티티로 관련 report_id → relevance_score 매핑."""
    report_scores: dict[int, float] = {}

    stock_codes = entities.get("stock_codes", [])
    keywords = entities.get("keywords", [])

    # 1. 종목 멘션으로 검색 (높은 관련도)
    if stock_codes:
        result = await session.execute(
            select(
                ReportStockMention.report_id,
                ReportStockMention.relevance_score,
            ).where(
                ReportStockMention.stock_code.in_(stock_codes)
            )
        )
        for row in result.all():
            rid, rel_score = row
            score = float(rel_score) if rel_score is not None else 0.5
            # primary mention 가중치
            existing = report_scores.get(rid, 0.0)
            report_scores[rid] = min(1.0, max(existing, score + 0.3))

    # 2. 섹터 멘션으로 검색
    if keywords:
        result = await session.execute(
            select(ReportSectorMention.report_id).where(
                ReportSectorMention.sector.in_(keywords)
            )
        )
        for row in result.all():
            rid = row[0]
            if rid not in report_scores:
                report_scores[rid] = 0.4

    # 3. 키워드 테이블로 검색
    if keywords:
        result = await session.execute(
            select(ReportKeyword.report_id).where(
                ReportKeyword.keyword.in_(keywords)
            )
        )
        for row in result.all():
            rid = row[0]
            if rid not in report_scores:
                report_scores[rid] = 0.3

    return report_scores


async def build_context(question: str, session: AsyncSession) -> str | None:
    """관련 리포트 검색 + analysis_data를 YAML로 변환 + 관련도/최신순 랭킹.

    Returns:
        YAML 형식으로 조립된 컨텍스트 문자열, 없으면 None.
    """
    entities = await extract_query_entities(question, session)

    has_entities = (
        entities["stock_codes"] or entities["keywords"]
    )

    if not has_entities:
        # 엔티티 없으면 최신 리포트로 폴백
        report_scores: dict[int, float] = {}
    else:
        report_scores = await _find_report_ids_by_entities(entities, session)

    # report_analysis가 있는 리포트만 (분석 완료)
    if report_scores:
        # 점수 있는 리포트 우선 조회
        ranked_ids = sorted(
            report_scores.keys(),
            key=lambda rid: report_scores[rid],
            reverse=True,
        )[:CONTEXT_BUDGET_MAX * 2]  # 여유분 조회

        result = await session.execute(
            select(Report, ReportAnalysis)
            .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
            .where(Report.id.in_(ranked_ids))
            .order_by(Report.report_date.desc())
        )
        rows = result.all()
    else:
        # 폴백: 최신 분석 리포트
        result = await session.execute(
            select(Report, ReportAnalysis)
            .join(ReportAnalysis, ReportAnalysis.report_id == Report.id)
            .order_by(Report.report_date.desc())
            .limit(CONTEXT_BUDGET_MAX)
        )
        rows = result.all()

    if not rows:
        log.debug("build_context: no reports found", question=question[:50])
        return None

    # 관련도 + 최신순 복합 랭킹
    def sort_key(row):
        report, analysis = row
        relevance = report_scores.get(report.id, 0.0)
        # 날짜를 0~1 정규화 (최신일수록 높음) — 간단 구현
        try:
            days_old = (datetime.date.today() - report.report_date).days
            recency = max(0.0, 1.0 - days_old / 365.0)
        except Exception:
            recency = 0.0
        return relevance * 0.7 + recency * 0.3

    rows_sorted = sorted(rows, key=sort_key, reverse=True)
    rows_sorted = rows_sorted[:CONTEXT_BUDGET_MAX]

    # YAML 변환
    context_blocks = []
    for report, analysis in rows_sorted:
        block = {
            "report_id": report.id,
            "broker": report.broker,
            "date": str(report.report_date),
            "title": report.title,
            "stock": report.stock_name,
            "stock_code": report.stock_code,
            "sector": report.sector,
            "opinion": report.opinion,
            "target_price": report.target_price,
            "analysis": analysis.analysis_data,
        }
        # None 필드 제거
        block = {k: v for k, v in block.items() if v is not None}
        context_blocks.append(block)

    context_str = yaml.dump(
        context_blocks,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )

    log.debug(
        "build_context_done",
        n_reports=len(context_blocks),
        entities=entities,
    )
    return context_str
