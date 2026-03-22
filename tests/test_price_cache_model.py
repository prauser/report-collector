"""Unit tests for PriceCache model and price_cache migration file.

These tests verify model structure and migration content without a live DB.
"""
import re
from pathlib import Path

import pytest
from sqlalchemy import BigInteger, Date, Numeric, String

from db.models import PriceCache


def _col(model, name):
    """Return the Column object by name from a mapped model."""
    return model.__table__.c[name]


# ---------------------------------------------------------------------------
# PriceCache model
# ---------------------------------------------------------------------------

class TestPriceCacheModel:
    def test_tablename(self):
        assert PriceCache.__tablename__ == "price_cache"

    def test_symbol_varchar20_pk(self):
        col = _col(PriceCache, "symbol")
        assert isinstance(col.type, String)
        assert col.type.length == 20
        assert not col.nullable
        assert col.primary_key

    def test_date_date_pk(self):
        col = _col(PriceCache, "date")
        assert isinstance(col.type, Date)
        assert not col.nullable
        assert col.primary_key

    def test_composite_pk_columns(self):
        pk_cols = {col.name for col in PriceCache.__table__.primary_key.columns}
        assert pk_cols == {"symbol", "date"}

    def test_open_numeric12_2_not_nullable(self):
        col = _col(PriceCache, "open")
        assert isinstance(col.type, Numeric)
        assert col.type.precision == 12
        assert col.type.scale == 2
        assert not col.nullable

    def test_high_numeric12_2_not_nullable(self):
        col = _col(PriceCache, "high")
        assert isinstance(col.type, Numeric)
        assert col.type.precision == 12
        assert col.type.scale == 2
        assert not col.nullable

    def test_low_numeric12_2_not_nullable(self):
        col = _col(PriceCache, "low")
        assert isinstance(col.type, Numeric)
        assert col.type.precision == 12
        assert col.type.scale == 2
        assert not col.nullable

    def test_close_numeric12_2_not_nullable(self):
        col = _col(PriceCache, "close")
        assert isinstance(col.type, Numeric)
        assert col.type.precision == 12
        assert col.type.scale == 2
        assert not col.nullable

    def test_volume_biginteger_not_nullable(self):
        col = _col(PriceCache, "volume")
        assert isinstance(col.type, BigInteger)
        assert not col.nullable

    def test_symbol_index_exists(self):
        index_names = {idx.name for idx in PriceCache.__table__.indexes}
        assert "ix_price_cache_symbol" in index_names

    def test_no_extra_pk_columns(self):
        """Ensure PK is exactly (symbol, date) — no surrogate key."""
        pk_cols = list(PriceCache.__table__.primary_key.columns)
        assert len(pk_cols) == 2


# ---------------------------------------------------------------------------
# Migration file content checks
# ---------------------------------------------------------------------------

_VERSIONS_DIR = Path(__file__).resolve().parents[1] / "db" / "migrations" / "versions"
_MIGRATION_FILE = _VERSIONS_DIR / "a8f3c1d2e945_add_price_cache.py"


class TestPriceCacheMigration:
    @pytest.fixture(scope="class")
    def migration_text(self):
        assert _MIGRATION_FILE.exists(), f"Migration file not found: {_MIGRATION_FILE}"
        return _MIGRATION_FILE.read_text()

    def test_revision_id(self, migration_text):
        assert "revision: str = 'a8f3c1d2e945'" in migration_text

    def test_down_revision_points_to_trade_tables(self, migration_text):
        assert "down_revision" in migration_text
        assert "f6e228957724" in migration_text

    def test_upgrade_creates_price_cache(self, migration_text):
        assert "create_table" in migration_text
        assert "'price_cache'" in migration_text

    def test_upgrade_has_composite_pk(self, migration_text):
        assert "PrimaryKeyConstraint('symbol', 'date')" in migration_text

    def test_upgrade_creates_symbol_index(self, migration_text):
        assert "ix_price_cache_symbol" in migration_text
        assert "create_index" in migration_text

    def test_downgrade_drops_price_cache(self, migration_text):
        assert "drop_table" in migration_text
        assert "price_cache" in migration_text

    def test_downgrade_drops_index(self, migration_text):
        assert "drop_index" in migration_text
        assert "ix_price_cache_symbol" in migration_text

    def test_no_alter_on_existing_tables(self, migration_text):
        """Migration must not modify any existing table."""
        alter_matches = re.findall(r"op\.alter_column\(['\"](\w+)", migration_text)
        add_col_matches = re.findall(r"op\.add_column\(['\"](\w+)", migration_text)
        drop_col_matches = re.findall(r"op\.drop_column\(['\"](\w+)", migration_text)
        existing_tables = {
            "reports", "stock_codes", "channels", "pending_messages",
            "backfill_runs", "report_markdown", "report_analysis",
            "report_stock_mentions", "report_sector_mentions", "report_keywords",
            "analysis_jobs", "trades", "trade_indicators", "trade_pairs",
            "chat_sessions", "chat_messages", "llm_usage",
        }
        for table in alter_matches + add_col_matches + drop_col_matches:
            assert table not in existing_tables, (
                f"Migration modifies existing table '{table}' — additive only required"
            )
