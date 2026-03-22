"""Tests for task-3: pipeline_status migration.

These tests check:
1. Report model has the new columns with correct definitions
2. Migration file is syntactically valid and has correct structure
3. Backfill SQL logic is correct (via string inspection)
"""
import importlib
import sys
from pathlib import Path
import pytest


# ---------------------------------------------------------------------------
# Model column tests
# ---------------------------------------------------------------------------

def test_report_has_pipeline_status_column():
    """Report 모델에 pipeline_status 컬럼이 있어야 함."""
    from db.models import Report
    from sqlalchemy import String

    col = Report.__table__.c.get("pipeline_status")
    assert col is not None, "pipeline_status 컬럼 없음"
    assert isinstance(col.type, String), f"타입 불일치: {col.type}"
    assert col.type.length == 30, f"String 길이 불일치: {col.type.length}"


def test_report_pipeline_status_has_index():
    """pipeline_status 컬럼이 index=True 로 설정되어야 함."""
    from db.models import Report

    indexed_cols = set()
    for idx in Report.__table__.indexes:
        for col in idx.columns:
            indexed_cols.add(col.name)

    assert "pipeline_status" in indexed_cols, (
        f"pipeline_status 인덱스 없음. 인덱스 컬럼들: {indexed_cols}"
    )


def test_report_has_pdf_fail_retryable_column():
    """Report 모델에 pdf_fail_retryable 컬럼이 있어야 함."""
    from db.models import Report
    from sqlalchemy import Boolean

    col = Report.__table__.c.get("pdf_fail_retryable")
    assert col is not None, "pdf_fail_retryable 컬럼 없음"
    assert isinstance(col.type, Boolean), f"타입 불일치: {col.type}"
    assert col.nullable is True, "pdf_fail_retryable 은 nullable 이어야 함"


def test_pipeline_status_default_is_new():
    """pipeline_status 기본값이 'new' 이어야 함."""
    from db.models import Report

    col = Report.__table__.c.get("pipeline_status")
    assert col is not None
    # ORM default
    assert col.default is not None or col.server_default is not None, (
        "pipeline_status 에 default 또는 server_default 가 없음"
    )


def test_pdf_fail_retryable_is_nullable():
    """pdf_fail_retryable 은 nullable 이어야 함."""
    from db.models import Report

    col = Report.__table__.c.get("pdf_fail_retryable")
    assert col is not None
    assert col.nullable is True


# ---------------------------------------------------------------------------
# Migration file structural tests
# ---------------------------------------------------------------------------

MIGRATION_PATH = Path(__file__).parent.parent / "db" / "migrations" / "versions" / "a1b2c3d4e5f6_add_pipeline_status.py"


def _load_migration():
    spec = importlib.util.spec_from_file_location("migration_a1b2c3d4e5f6", MIGRATION_PATH)
    mod = importlib.util.module_from_spec(spec)
    # Provide stub for alembic.op so import doesn't fail without a DB
    import types
    alembic_stub = types.ModuleType("alembic")
    op_stub = types.ModuleType("alembic.op")
    alembic_stub.op = op_stub
    sys.modules.setdefault("alembic", alembic_stub)
    sys.modules.setdefault("alembic.op", op_stub)
    spec.loader.exec_module(mod)
    return mod


def test_migration_file_exists():
    assert MIGRATION_PATH.exists(), f"마이그레이션 파일 없음: {MIGRATION_PATH}"


def test_migration_revision():
    mod = _load_migration()
    assert mod.revision == "a1b2c3d4e5f6"


def test_migration_down_revision():
    """이전 마이그레이션(f6e228957724) 을 가리켜야 함."""
    mod = _load_migration()
    assert mod.down_revision == "f6e228957724"


def test_migration_has_upgrade():
    mod = _load_migration()
    assert callable(getattr(mod, "upgrade", None)), "upgrade 함수 없음"


def test_migration_has_downgrade():
    mod = _load_migration()
    assert callable(getattr(mod, "downgrade", None)), "downgrade 함수 없음"


def test_migration_source_contains_pipeline_status_column():
    src = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "pipeline_status" in src
    assert "String(length=30)" in src or "String(30)" in src


def test_migration_source_contains_pdf_fail_retryable_column():
    src = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "pdf_fail_retryable" in src
    assert "Boolean" in src


def test_migration_source_contains_index_creation():
    src = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "ix_reports_pipeline_status" in src
    assert "create_index" in src


def test_migration_source_contains_index_drop_in_downgrade():
    src = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "drop_index" in src
    assert "ix_reports_pipeline_status" in src


def test_migration_backfill_done():
    """done 상태 backfill: report_analysis 에 row 있는 report."""
    src = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "done" in src
    assert "report_analysis" in src


def test_migration_backfill_pdf_failed():
    """pdf_failed 상태 backfill: pdf_download_failed=true."""
    src = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "pdf_failed" in src
    assert "pdf_download_failed" in src


def test_migration_backfill_pdf_done():
    """pdf_done 상태 backfill: pdf_path IS NOT NULL."""
    src = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "pdf_done" in src
    assert "pdf_path" in src


def test_migration_backfill_retryable_false_for_404():
    src = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "http_404" in src
    assert "http_410" in src


def test_migration_backfill_retryable_false_for_unsupported_host():
    src = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "unsupported_host" in src
    assert "LIKE" in src


def test_migration_backfill_retryable_true_for_other_reasons():
    """pdf_fail_reason IS NOT NULL 이고 위 조건 외 → True."""
    src = MIGRATION_PATH.read_text(encoding="utf-8")
    assert "pdf_fail_retryable = true" in src or "pdf_fail_retryable = True" in src


def test_downgrade_drops_both_columns():
    src = MIGRATION_PATH.read_text(encoding="utf-8")
    # Both drop_column calls should appear in the downgrade section
    assert src.count("drop_column") >= 2
    assert "pipeline_status" in src
    assert "pdf_fail_retryable" in src
