"""Tests for parser/meta_updater.py — apply_key_data_meta and apply_layer2_meta."""
import pytest
from datetime import date
from unittest.mock import MagicMock

from parser.meta_updater import apply_key_data_meta, apply_layer2_meta, trunc


# ──────────────────────────────────────────────
# trunc helper
# ──────────────────────────────────────────────

class TestTrunc:

    def test_truncates_long_string(self):
        assert trunc("abcde", 3) == "abc"

    def test_does_not_truncate_short_string(self):
        assert trunc("ab", 5) == "ab"

    def test_exact_length_not_truncated(self):
        assert trunc("abc", 3) == "abc"

    def test_non_string_passthrough(self):
        assert trunc(12345, 3) == 12345
        assert trunc(None, 10) is None

    def test_empty_string(self):
        assert trunc("", 5) == ""


# ──────────────────────────────────────────────
# apply_key_data_meta
# ──────────────────────────────────────────────

def _make_key_data(**kwargs):
    """Create a MagicMock key_data object with given attributes."""
    kd = MagicMock()
    # default all fields to None
    for field in ("broker", "analyst", "stock_name", "stock_code",
                  "opinion", "target_price", "report_type", "title", "date"):
        setattr(kd, field, None)
    for k, v in kwargs.items():
        setattr(kd, k, v)
    return kd


class TestApplyKeyDataMeta:

    def test_none_key_data_returns_empty(self):
        assert apply_key_data_meta(None) == {}

    def test_all_none_fields_returns_empty(self):
        kd = _make_key_data()
        assert apply_key_data_meta(kd) == {}

    def test_broker_normalized(self):
        kd = _make_key_data(broker="미래에셋")
        result = apply_key_data_meta(kd)
        assert result["broker"] == "미래에셋증권"

    def test_broker_no_alias_passthrough(self):
        kd = _make_key_data(broker="삼성증권")
        result = apply_key_data_meta(kd)
        assert result["broker"] == "삼성증권"

    def test_opinion_normalized(self):
        kd = _make_key_data(opinion="Buy")
        result = apply_key_data_meta(kd)
        assert result["opinion"] == "매수"

    def test_opinion_buy_lower_normalized(self):
        kd = _make_key_data(opinion="buy")
        result = apply_key_data_meta(kd)
        assert result["opinion"] == "매수"

    def test_opinion_hold_normalized(self):
        kd = _make_key_data(opinion="HOLD")
        result = apply_key_data_meta(kd)
        assert result["opinion"] == "중립"

    def test_opinion_no_alias_passthrough(self):
        kd = _make_key_data(opinion="매수")
        result = apply_key_data_meta(kd)
        assert result["opinion"] == "매수"

    def test_broker_truncated_at_50(self):
        long_broker = "증" * 60
        kd = _make_key_data(broker=long_broker)
        result = apply_key_data_meta(kd)
        assert len(result["broker"]) == 50

    def test_analyst_truncated_at_100(self):
        kd = _make_key_data(analyst="A" * 150)
        result = apply_key_data_meta(kd)
        assert len(result["analyst"]) == 100

    def test_stock_name_truncated_at_100(self):
        kd = _make_key_data(stock_name="X" * 120)
        result = apply_key_data_meta(kd)
        assert len(result["stock_name"]) == 100

    def test_opinion_truncated_at_20(self):
        # opinion longer than 20 chars after normalization would be truncated
        kd = _make_key_data(opinion="A" * 25)
        result = apply_key_data_meta(kd)
        assert len(result["opinion"]) == 20

    def test_title_truncated_at_500(self):
        kd = _make_key_data(title="T" * 600)
        result = apply_key_data_meta(kd)
        assert len(result["title"]) == 500

    def test_report_type_truncated_at_50(self):
        kd = _make_key_data(report_type="R" * 60)
        result = apply_key_data_meta(kd)
        assert len(result["report_type"]) == 50

    def test_stock_code_passthrough(self):
        kd = _make_key_data(stock_code="005930")
        result = apply_key_data_meta(kd)
        assert result["stock_code"] == "005930"

    def test_target_price_passthrough(self):
        kd = _make_key_data(target_price=85000)
        result = apply_key_data_meta(kd)
        assert result["target_price"] == 85000

    def test_parsed_date_included(self):
        kd = _make_key_data(stock_name="삼성전자")
        d = date(2026, 1, 15)
        result = apply_key_data_meta(kd, parsed_date=d)
        assert result["report_date"] == d

    def test_parsed_date_none_excluded(self):
        kd = _make_key_data(stock_name="삼성전자")
        result = apply_key_data_meta(kd, parsed_date=None)
        assert "report_date" not in result

    def test_falsy_values_excluded(self):
        """Empty string, 0, None should all be excluded from result."""
        kd = _make_key_data(broker="", analyst=None, target_price=0)
        result = apply_key_data_meta(kd)
        assert "broker" not in result
        assert "analyst" not in result
        assert "target_price" not in result

    def test_all_fields_populated(self):
        kd = _make_key_data(
            broker="KB",
            analyst="홍길동",
            stock_name="삼성전자",
            stock_code="005930",
            opinion="BUY",
            target_price=90000,
            report_type="기업분석",
            title="삼성전자 리포트",
        )
        d = date(2026, 1, 1)
        result = apply_key_data_meta(kd, parsed_date=d)
        assert result["broker"] == "KB증권"  # normalized
        assert result["analyst"] == "홍길동"
        assert result["stock_name"] == "삼성전자"
        assert result["stock_code"] == "005930"
        assert result["opinion"] == "매수"  # normalized
        assert result["target_price"] == 90000
        assert result["report_type"] == "기업분석"
        assert result["title"] == "삼성전자 리포트"
        assert result["report_date"] == d

    def test_broker_normalization_applied_before_truncation(self):
        """normalize_broker runs first, then the result is truncated to 50 chars."""
        kd = _make_key_data(broker="한투")
        result = apply_key_data_meta(kd)
        assert result["broker"] == "한국투자증권"

    def test_returns_dict(self):
        kd = _make_key_data(broker="삼성증권")
        result = apply_key_data_meta(kd)
        assert isinstance(result, dict)


# ──────────────────────────────────────────────
# apply_layer2_meta (from meta_updater, not listener)
# ──────────────────────────────────────────────

class TestApplyLayer2MetaFromModule:
    """Verify apply_layer2_meta imported from parser.meta_updater works."""

    def test_broker_normalized(self):
        report = MagicMock()
        updates = apply_layer2_meta(report, {"broker": "한투"})
        assert updates["broker"] == "한국투자증권"

    def test_opinion_normalized(self):
        report = MagicMock()
        updates = apply_layer2_meta(report, {"opinion": "Buy"})
        assert updates["opinion"] == "매수"

    def test_empty_meta_returns_empty(self):
        updates = apply_layer2_meta(MagicMock(), {})
        assert updates == {}

    def test_none_meta_returns_empty(self):
        updates = apply_layer2_meta(MagicMock(), None)
        assert updates == {}

    def test_string_target_price_parsed(self):
        updates = apply_layer2_meta(MagicMock(), {"target_price": "85,000원"})
        assert updates["target_price"] == 85000

    def test_int_target_price(self):
        updates = apply_layer2_meta(MagicMock(), {"target_price": 90000})
        assert updates["target_price"] == 90000

    def test_prev_opinion_normalized(self):
        updates = apply_layer2_meta(MagicMock(), {"prev_opinion": "HOLD"})
        assert updates["prev_opinion"] == "중립"

    def test_sector_included(self):
        updates = apply_layer2_meta(MagicMock(), {"sector": "반도체"})
        assert updates["sector"] == "반도체"
