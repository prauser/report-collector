"""Tests for agent/context_builder.py."""
from __future__ import annotations

import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from agent.context_builder import (
    CONTEXT_BUDGET_MAX,
    _extract_sector_keywords,
    build_context,
    extract_query_entities,
)


# ──────────────────────────────────────────────
# Helper factories
# ──────────────────────────────────────────────

def make_stock_code(code="005930", name="삼성전자", name_normalized="삼성전자"):
    sc = MagicMock()
    sc.code = code
    sc.name = name
    sc.name_normalized = name_normalized
    return sc


def make_report(
    report_id=1,
    broker="미래에셋",
    report_date=None,
    title="삼성전자 분석",
    stock_name="삼성전자",
    stock_code="005930",
    sector="반도체",
    opinion="BUY",
    target_price=90000,
):
    r = MagicMock()
    r.id = report_id
    r.broker = broker
    r.report_date = report_date or datetime.date.today()
    r.title = title
    r.stock_name = stock_name
    r.stock_code = stock_code
    r.sector = sector
    r.opinion = opinion
    r.target_price = target_price
    return r


def make_analysis(report_id=1, analysis_data=None):
    a = MagicMock()
    a.report_id = report_id
    a.analysis_data = analysis_data or {"summary": "좋음", "target": 90000}
    return a


def scalars_result(items):
    """scalars().all() → items 반환하는 mock."""
    r = MagicMock()
    r.scalars.return_value.all.return_value = items
    return r


def rows_result(rows):
    """.all() → rows 반환하는 mock."""
    r = MagicMock()
    r.all.return_value = rows
    return r


# ──────────────────────────────────────────────
# _extract_sector_keywords
# ──────────────────────────────────────────────

class TestExtractSectorKeywords:
    def test_extracts_korean_tokens(self):
        tokens = ["삼성전자", "반도체", "매수", "005930"]
        kws = _extract_sector_keywords("삼성전자 반도체 매수", tokens)
        assert "삼성전자" in kws
        assert "반도체" in kws
        assert "매수" in kws
        # 종목코드(숫자) 제외
        assert "005930" not in kws

    def test_excludes_short_tokens(self):
        tokens = ["삼", "성", "전자"]
        kws = _extract_sector_keywords("삼 성 전자", tokens)
        # 1자 제외, 2자 이상만
        assert "삼" not in kws
        assert "성" not in kws
        assert "전자" in kws

    def test_deduplicates(self):
        tokens = ["반도체", "반도체", "반도체"]
        kws = _extract_sector_keywords("반도체 반도체", tokens)
        assert kws.count("반도체") == 1

    def test_max_20_keywords(self):
        tokens = [f"키워드{i:02d}" for i in range(30)]
        question = " ".join(tokens)
        kws = _extract_sector_keywords(question, tokens)
        assert len(kws) <= 20

    def test_empty_question(self):
        kws = _extract_sector_keywords("", [])
        assert kws == []


# ──────────────────────────────────────────────
# extract_query_entities
# ──────────────────────────────────────────────

class TestExtractQueryEntities:
    @pytest.mark.asyncio
    async def test_extracts_stock_code_from_query(self):
        """6자리 종목코드 직접 언급 시 DB에서 매칭."""
        samsung = make_stock_code("005930", "삼성전자")
        session = AsyncMock()

        # execute 1: code search → samsung matched
        # execute 2: name search → empty (already matched by code)
        session.execute = AsyncMock(side_effect=[
            scalars_result([samsung]),   # code IN [005930]
            scalars_result([]),          # name IN tokens
        ])

        result = await extract_query_entities("005930 종목 분석해줘", session)

        assert "005930" in result["stock_codes"]
        assert "삼성전자" in result["stock_names"]

    @pytest.mark.asyncio
    async def test_extracts_stock_name_from_query(self):
        """종목명 텍스트 언급 시 DB에서 매칭.

        질문에 6자리 숫자 없음 → code execute 스킵.
        name 검색 execute 1번.
        """
        samsung = make_stock_code("005930", "삼성전자")
        session = AsyncMock()

        # execute 1 only: name search (no 6-digit codes in question)
        session.execute = AsyncMock(side_effect=[
            scalars_result([samsung]),   # name IN tokens
        ])

        result = await extract_query_entities("삼성전자 목표주가 알려줘", session)

        assert "005930" in result["stock_codes"]
        assert "삼성전자" in result["stock_names"]

    @pytest.mark.asyncio
    async def test_no_match_returns_empty(self):
        """매칭 없을 때 빈 리스트 반환."""
        session = AsyncMock()
        # execute 1: name search → empty
        session.execute = AsyncMock(side_effect=[
            scalars_result([]),
        ])

        result = await extract_query_entities("오늘 날씨 어때", session)

        assert result["stock_codes"] == []
        assert result["stock_names"] == []

    @pytest.mark.asyncio
    async def test_returns_keywords_for_korean_tokens(self):
        """한글 토큰을 키워드로 추출."""
        session = AsyncMock()
        # execute 1: name search → empty
        session.execute = AsyncMock(side_effect=[
            scalars_result([]),
        ])

        result = await extract_query_entities("반도체 섹터 전망 알려줘", session)

        assert len(result["keywords"]) > 0
        assert "반도체" in result["keywords"]

    @pytest.mark.asyncio
    async def test_no_duplicate_stock_codes(self):
        """같은 종목이 코드+이름으로 중복 매칭되지 않음."""
        samsung = make_stock_code("005930", "삼성전자")
        session = AsyncMock()

        # execute 1: code search → samsung
        # execute 2: name search → samsung again (but already matched)
        session.execute = AsyncMock(side_effect=[
            scalars_result([samsung]),   # code match
            scalars_result([samsung]),   # name match (duplicate)
        ])

        result = await extract_query_entities("005930 삼성전자 분석", session)

        assert result["stock_codes"].count("005930") == 1


# ──────────────────────────────────────────────
# build_context
# ──────────────────────────────────────────────

class TestBuildContext:
    """build_context 테스트.

    execute 호출 순서 (엔티티 없는 경우, 한글 키워드만):
      1. extract_query_entities: name search (no 6-digit codes → code search skipped)
      2. _find_report_ids_by_entities: no stock_codes → skip; keywords → sector search
      3. _find_report_ids_by_entities: keywords → keyword search
      4. report+analysis query

    execute 호출 순서 (빈 질문 / 키워드 없는 경우):
      (키워드 2자 이상 없음 → name search execute 없을 수도 있음)
      1. (no tokens → name search execute skipped)
      Fallback: report+analysis query
    """

    def _make_row(self, report_id=1, report_date=None):
        r = make_report(report_id=report_id, report_date=report_date)
        a = make_analysis(report_id=report_id)
        return (r, a)

    @pytest.mark.asyncio
    async def test_returns_yaml_string_with_reports(self):
        """리포트가 있을 때 YAML 문자열 반환 (키워드 있는 질문)."""
        row = self._make_row()
        session = AsyncMock()

        session.execute = AsyncMock(side_effect=[
            scalars_result([]),   # name search
            rows_result([]),      # sector mention (keywords 검색)
            rows_result([]),      # keyword mention
            rows_result([row]),   # fallback report+analysis
        ])

        ctx = await build_context("반도체 전망", session)

        assert ctx is not None
        parsed = yaml.safe_load(ctx)
        assert isinstance(parsed, list)
        assert len(parsed) == 1
        assert parsed[0]["broker"] == "미래에셋"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_reports(self):
        """리포트가 없을 때 None 반환."""
        session = AsyncMock()

        session.execute = AsyncMock(side_effect=[
            scalars_result([]),   # name search
            rows_result([]),      # sector mention
            rows_result([]),      # keyword mention
            rows_result([]),      # report+analysis
        ])

        ctx = await build_context("반도체 전망", session)
        assert ctx is None

    @pytest.mark.asyncio
    async def test_context_budget_max_respected(self):
        """CONTEXT_BUDGET_MAX를 초과하지 않음."""
        rows = [self._make_row(report_id=i, report_date=datetime.date.today()) for i in range(30)]
        session = AsyncMock()

        session.execute = AsyncMock(side_effect=[
            scalars_result([]),   # name search
            rows_result([]),      # sector mention
            rows_result([]),      # keyword mention
            rows_result(rows),    # fallback report+analysis (30개)
        ])

        ctx = await build_context("반도체 분석", session)

        assert ctx is not None
        parsed = yaml.safe_load(ctx)
        assert len(parsed) <= CONTEXT_BUDGET_MAX

    @pytest.mark.asyncio
    async def test_yaml_contains_required_fields(self):
        """YAML 블록에 필수 필드 포함."""
        row = self._make_row()
        session = AsyncMock()

        session.execute = AsyncMock(side_effect=[
            scalars_result([]),   # name search
            rows_result([]),      # sector mention
            rows_result([]),      # keyword mention
            rows_result([row]),   # report+analysis
        ])

        ctx = await build_context("반도체 분석", session)
        parsed = yaml.safe_load(ctx)
        block = parsed[0]

        assert "broker" in block
        assert "date" in block
        assert "title" in block
        assert "analysis" in block

    @pytest.mark.asyncio
    async def test_none_fields_excluded_from_yaml(self):
        """None 값 필드는 YAML에서 제외."""
        row = self._make_row()
        row[0].sector = None
        row[0].opinion = None

        session = AsyncMock()

        session.execute = AsyncMock(side_effect=[
            scalars_result([]),   # name search
            rows_result([]),      # sector mention
            rows_result([]),      # keyword mention
            rows_result([row]),   # report+analysis
        ])

        ctx = await build_context("반도체 분석", session)
        parsed = yaml.safe_load(ctx)
        block = parsed[0]

        assert "sector" not in block
        assert "opinion" not in block

    @pytest.mark.asyncio
    async def test_stock_match_uses_relevance_scores(self):
        """종목 매칭 시 관련도 점수 기반 정렬."""
        session = AsyncMock()

        samsung = make_stock_code("005930", "삼성전자")
        row = self._make_row(report_id=1)

        session.execute = AsyncMock(side_effect=[
            scalars_result([samsung]),               # code search
            scalars_result([]),                      # name search
            rows_result([(1, Decimal("0.9"))]),      # stock mention
            rows_result([]),                         # sector mention
            rows_result([]),                         # keyword mention
            rows_result([row]),                      # report+analysis
        ])

        ctx = await build_context("005930 삼성전자 목표주가", session)
        assert ctx is not None
        parsed = yaml.safe_load(ctx)
        assert len(parsed) == 1

    @pytest.mark.asyncio
    async def test_fallback_when_no_entities(self):
        """엔티티 없을 때 최신 리포트로 폴백.

        질문에 2자 이상 토큰 없음 → name search execute 스킵.
        has_entities=False → _find_report_ids_by_entities 스킵.
        바로 fallback report+analysis 쿼리.
        """
        row = self._make_row()
        session = AsyncMock()

        # Only 1 execute: fallback report+analysis query
        session.execute = AsyncMock(side_effect=[
            rows_result([row]),
        ])

        ctx = await build_context("a", session)  # 1자 짜리 질문 → 토큰 없음
        assert ctx is not None
        parsed = yaml.safe_load(ctx)
        assert len(parsed) == 1
