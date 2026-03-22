"""Unit tests for Trade, TradeIndicator, TradePair models.

These tests verify model structure without a live DB connection.
"""
import pytest
from decimal import Decimal
from sqlalchemy import (
    BigInteger, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB

from db.models import Trade, TradeIndicator, TradePair


def _col(model, name):
    """Return the Column object by name from a mapped model."""
    return model.__table__.c[name]


def _get_constraint(model, cls, **kwargs):
    """Return the first constraint of `cls` whose columns match kwargs['columns'] or name matches kwargs['name']."""
    for c in model.__table__.constraints:
        if isinstance(c, cls):
            if "name" in kwargs and c.name == kwargs["name"]:
                return c
            if "columns" in kwargs:
                col_names = {col.name for col in c.columns}
                if col_names == set(kwargs["columns"]):
                    return c
    return None


# ---------------------------------------------------------------------------
# Trade model
# ---------------------------------------------------------------------------

class TestTradeModel:
    def test_tablename(self):
        assert Trade.__tablename__ == "trades"

    def test_pk_is_biginteger(self):
        col = _col(Trade, "id")
        assert col.primary_key
        assert isinstance(col.type, BigInteger)

    def test_symbol_varchar20(self):
        col = _col(Trade, "symbol")
        assert isinstance(col.type, String)
        assert col.type.length == 20
        assert not col.nullable

    def test_name_varchar100(self):
        col = _col(Trade, "name")
        assert isinstance(col.type, String)
        assert col.type.length == 100
        assert not col.nullable

    def test_side_varchar4(self):
        col = _col(Trade, "side")
        assert isinstance(col.type, String)
        assert col.type.length == 4
        assert not col.nullable

    def test_traded_at_timestamptz(self):
        col = _col(Trade, "traded_at")
        assert isinstance(col.type, DateTime)
        assert col.type.timezone
        assert not col.nullable

    def test_price_numeric12_2(self):
        col = _col(Trade, "price")
        assert isinstance(col.type, Numeric)
        assert col.type.precision == 12
        assert col.type.scale == 2
        assert not col.nullable

    def test_quantity_integer(self):
        col = _col(Trade, "quantity")
        assert isinstance(col.type, Integer)
        assert not col.nullable

    def test_amount_numeric14_2(self):
        col = _col(Trade, "amount")
        assert isinstance(col.type, Numeric)
        assert col.type.precision == 14
        assert col.type.scale == 2
        assert not col.nullable

    def test_broker_varchar20(self):
        col = _col(Trade, "broker")
        assert isinstance(col.type, String)
        assert col.type.length == 20
        assert not col.nullable

    def test_account_type_varchar20(self):
        col = _col(Trade, "account_type")
        assert isinstance(col.type, String)
        assert col.type.length == 20
        assert not col.nullable

    def test_market_varchar10(self):
        col = _col(Trade, "market")
        assert isinstance(col.type, String)
        assert col.type.length == 10
        assert not col.nullable

    def test_fees_nullable_numeric10_2(self):
        col = _col(Trade, "fees")
        assert isinstance(col.type, Numeric)
        assert col.type.precision == 10
        assert col.type.scale == 2
        assert col.nullable

    def test_reason_nullable_text(self):
        col = _col(Trade, "reason")
        assert isinstance(col.type, Text)
        assert col.nullable

    def test_review_nullable_text(self):
        col = _col(Trade, "review")
        assert isinstance(col.type, Text)
        assert col.nullable

    def test_created_at_has_server_default(self):
        col = _col(Trade, "created_at")
        assert isinstance(col.type, DateTime)
        assert col.type.timezone
        assert col.server_default is not None

    def test_unique_constraint_dedup(self):
        uc = _get_constraint(Trade, UniqueConstraint, name="uq_trade_dedup")
        assert uc is not None, "uq_trade_dedup unique constraint not found"
        col_names = {col.name for col in uc.columns}
        assert col_names == {"symbol", "traded_at", "side", "price", "quantity", "broker"}

    def test_indexes_exist(self):
        index_names = {idx.name for idx in Trade.__table__.indexes}
        assert "ix_trades_symbol" in index_names
        assert "ix_trades_traded_at" in index_names
        assert "ix_trades_side" in index_names


# ---------------------------------------------------------------------------
# TradeIndicator model
# ---------------------------------------------------------------------------

class TestTradeIndicatorModel:
    def test_tablename(self):
        assert TradeIndicator.__tablename__ == "trade_indicators"

    def test_pk_is_biginteger(self):
        col = _col(TradeIndicator, "id")
        assert col.primary_key
        assert isinstance(col.type, BigInteger)

    def test_trade_id_fk_unique_not_nullable(self):
        col = _col(TradeIndicator, "trade_id")
        assert not col.nullable
        # unique constraint
        from sqlalchemy import UniqueConstraint as UC
        unique_cols = set()
        for c in TradeIndicator.__table__.constraints:
            if isinstance(c, UC):
                cols = {cc.name for cc in c.columns}
                if "trade_id" in cols and len(cols) == 1:
                    unique_cols.add("trade_id")
        assert "trade_id" in unique_cols, "trade_id should have a UNIQUE constraint (1:1)"

    def test_trade_id_fk_cascade(self):
        col = _col(TradeIndicator, "trade_id")
        fk = list(col.foreign_keys)[0]
        assert fk.column.table.name == "trades"
        assert fk.ondelete == "CASCADE"

    def test_stoch_k_d_jsonb_nullable(self):
        col = _col(TradeIndicator, "stoch_k_d")
        assert isinstance(col.type, JSONB)
        assert col.nullable

    def test_rsi_14_numeric5_2_nullable(self):
        col = _col(TradeIndicator, "rsi_14")
        assert isinstance(col.type, Numeric)
        assert col.type.precision == 5
        assert col.type.scale == 2
        assert col.nullable

    def test_macd_jsonb_nullable(self):
        col = _col(TradeIndicator, "macd")
        assert isinstance(col.type, JSONB)
        assert col.nullable

    def test_ma_position_jsonb_nullable(self):
        col = _col(TradeIndicator, "ma_position")
        assert isinstance(col.type, JSONB)
        assert col.nullable

    def test_bb_position_numeric5_4_nullable(self):
        col = _col(TradeIndicator, "bb_position")
        assert isinstance(col.type, Numeric)
        assert col.type.precision == 5
        assert col.type.scale == 4
        assert col.nullable

    def test_volume_ratio_numeric8_2_nullable(self):
        col = _col(TradeIndicator, "volume_ratio")
        assert isinstance(col.type, Numeric)
        assert col.type.precision == 8
        assert col.type.scale == 2
        assert col.nullable

    def test_snapshot_text_nullable(self):
        col = _col(TradeIndicator, "snapshot_text")
        assert isinstance(col.type, Text)
        assert col.nullable


# ---------------------------------------------------------------------------
# TradePair model
# ---------------------------------------------------------------------------

class TestTradePairModel:
    def test_tablename(self):
        assert TradePair.__tablename__ == "trade_pairs"

    def test_pk_is_biginteger(self):
        col = _col(TradePair, "id")
        assert col.primary_key
        assert isinstance(col.type, BigInteger)

    def test_buy_trade_id_fk_cascade(self):
        col = _col(TradePair, "buy_trade_id")
        assert not col.nullable
        fk = list(col.foreign_keys)[0]
        assert fk.column.table.name == "trades"
        assert fk.ondelete == "CASCADE"

    def test_sell_trade_id_fk_cascade(self):
        col = _col(TradePair, "sell_trade_id")
        assert not col.nullable
        fk = list(col.foreign_keys)[0]
        assert fk.column.table.name == "trades"
        assert fk.ondelete == "CASCADE"

    def test_profit_rate_numeric8_4_nullable(self):
        col = _col(TradePair, "profit_rate")
        assert isinstance(col.type, Numeric)
        assert col.type.precision == 8
        assert col.type.scale == 4
        assert col.nullable

    def test_holding_days_integer_nullable(self):
        col = _col(TradePair, "holding_days")
        assert isinstance(col.type, Integer)
        assert col.nullable

    def test_indexes_exist(self):
        index_names = {idx.name for idx in TradePair.__table__.indexes}
        assert "ix_trade_pairs_buy_trade_id" in index_names
        assert "ix_trade_pairs_sell_trade_id" in index_names
