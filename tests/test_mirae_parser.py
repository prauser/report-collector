"""Unit tests for MiraeParser and resolve_stock_codes.

All tests run without a live DB or file system — pure logic only.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from trades.csv_parsers.mirae import MiraeParser, _parse_quantity
from trades.csv_parsers.common import resolve_stock_codes, TradeRow


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BOM = "\ufeff"  # will be encoded into the bytes via utf-8-sig


def _make_csv(rows: list[str], bom: bool = True) -> bytes:
    """Build a Mirae-style CSV bytes object from a list of line strings.

    The standard Mirae CSV layout:
        row 0 — title line
        row 1 — empty
        row 2 — empty
        row 3 — empty
        row 4 — header
        row 5+ — data
    """
    header_and_data = "\n".join(rows)
    preamble = "미래에셋증권 거래내역\n\n\n\n"
    content = preamble + header_and_data
    raw = content.encode("utf-8")
    if bom:
        raw = b"\xef\xbb\xbf" + raw
    return raw


# Canonical header line
HEADER = "거래일자,거래종류,종목명,거래수량,거래금액,외화거래금액,수수료,예수금잔고"

# A typical buy row: 유통융자매수입고
BUY_ROW = "2026.03.20,유통융자매수입고,삼성전자,\"1,500\",\"16,362,500\",,\"2,359.00\",\"1,234,567\""

# A typical sell row: 자기융자매도상환
SELL_ROW = "2026.03.21,자기융자매도상환,삼성전자,\"1,500\",\"16,500,000\",,\"2,400.00\",\"1,100,000\""

# A fund-transfer row (대금입금) — should be skipped
TRANSFER_ROW = "2026.03.21,유통융자대금입금,,0,\"16,362,500\",,0,\"17,597,067\""

# A zero-quantity row without a name — also skipped
ZERO_QTY_ROW = "2026.03.22,유통융자대금출금,,0,\"5,000,000\",,0,\"12,597,067\""


# ---------------------------------------------------------------------------
# TestMiraeParserNormal
# ---------------------------------------------------------------------------

class TestMiraeParserNormal:
    def test_buy_row_parsed(self):
        csv_bytes = _make_csv([HEADER, BUY_ROW])
        parser = MiraeParser()
        rows = parser.parse(csv_bytes)
        assert len(rows) == 1
        row = rows[0]
        assert row.side == "buy"
        assert row.name == "삼성전자"
        assert row.broker == "미래에셋"

    def test_sell_row_parsed(self):
        csv_bytes = _make_csv([HEADER, SELL_ROW])
        parser = MiraeParser()
        rows = parser.parse(csv_bytes)
        assert len(rows) == 1
        assert rows[0].side == "sell"

    def test_transfer_row_skipped(self):
        csv_bytes = _make_csv([HEADER, TRANSFER_ROW])
        parser = MiraeParser()
        rows = parser.parse(csv_bytes)
        assert rows == []

    def test_zero_qty_row_skipped(self):
        csv_bytes = _make_csv([HEADER, ZERO_QTY_ROW])
        parser = MiraeParser()
        rows = parser.parse(csv_bytes)
        assert rows == []

    def test_mixed_rows(self):
        """Buy + sell + two transfer rows → only 2 trade rows returned."""
        csv_bytes = _make_csv([HEADER, BUY_ROW, TRANSFER_ROW, SELL_ROW, ZERO_QTY_ROW])
        rows = MiraeParser().parse(csv_bytes)
        assert len(rows) == 2
        assert rows[0].side == "buy"
        assert rows[1].side == "sell"

    def test_quantity_parsed_with_commas(self):
        csv_bytes = _make_csv([HEADER, BUY_ROW])
        row = MiraeParser().parse(csv_bytes)[0]
        assert row.quantity == 1500

    def test_amount_parsed_with_commas(self):
        csv_bytes = _make_csv([HEADER, BUY_ROW])
        row = MiraeParser().parse(csv_bytes)[0]
        assert row.amount == Decimal("16362500")

    def test_fees_parsed_with_commas_and_decimal(self):
        csv_bytes = _make_csv([HEADER, BUY_ROW])
        row = MiraeParser().parse(csv_bytes)[0]
        assert row.fees == Decimal("2359.00")

    def test_price_calculated_from_amount_over_quantity(self):
        # 16,362,500 / 1500 = 10908.33...
        csv_bytes = _make_csv([HEADER, BUY_ROW])
        row = MiraeParser().parse(csv_bytes)[0]
        expected = (Decimal("16362500") / Decimal("1500")).quantize(Decimal("0.01"))
        assert row.price == expected

    def test_traded_at_is_datetime(self):
        csv_bytes = _make_csv([HEADER, BUY_ROW])
        row = MiraeParser().parse(csv_bytes)[0]
        assert isinstance(row.traded_at, datetime)
        assert row.traded_at == datetime(2026, 3, 20, 0, 0, 0)

    def test_symbol_is_empty_string(self):
        csv_bytes = _make_csv([HEADER, BUY_ROW])
        row = MiraeParser().parse(csv_bytes)[0]
        assert row.symbol == ""

    def test_market_is_none(self):
        csv_bytes = _make_csv([HEADER, BUY_ROW])
        row = MiraeParser().parse(csv_bytes)[0]
        assert row.market is None


# ---------------------------------------------------------------------------
# TestMiraeParserAccountType
# ---------------------------------------------------------------------------

class TestMiraeParserAccountType:
    def test_yukong_account_type_buy(self):
        csv_bytes = _make_csv([HEADER, BUY_ROW])  # 유통융자매수입고
        row = MiraeParser().parse(csv_bytes)[0]
        assert row.account_type == "유통융자"

    def test_jagi_account_type_sell(self):
        csv_bytes = _make_csv([HEADER, SELL_ROW])  # 자기융자매도상환
        row = MiraeParser().parse(csv_bytes)[0]
        assert row.account_type == "자기융자"

    def test_jagi_buy_account_type(self):
        row_str = "2026.03.20,자기융자매수입고,현대차,100,\"1,500,000\",,150.00,\"5,000,000\""
        csv_bytes = _make_csv([HEADER, row_str])
        row = MiraeParser().parse(csv_bytes)[0]
        assert row.account_type == "자기융자"

    def test_yukong_sell_account_type(self):
        row_str = "2026.03.21,유통융자매도상환,현대차,100,\"1,600,000\",,160.00,\"5,100,000\""
        csv_bytes = _make_csv([HEADER, row_str])
        row = MiraeParser().parse(csv_bytes)[0]
        assert row.account_type == "유통융자"


# ---------------------------------------------------------------------------
# TestMiraeParserEdgeCases
# ---------------------------------------------------------------------------

class TestMiraeParserEdgeCases:
    def test_empty_bytes_returns_empty_list(self):
        rows = MiraeParser().parse(b"")
        assert rows == []

    def test_header_only_returns_empty_list(self):
        csv_bytes = _make_csv([HEADER])
        rows = MiraeParser().parse(csv_bytes)
        assert rows == []

    def test_no_header_row_returns_empty_list(self):
        # CSV with no line starting with 거래일자
        raw = b"\xef\xbb\xbf" + "col1,col2,col3\nval1,val2,val3\n".encode("utf-8")
        rows = MiraeParser().parse(raw)
        assert rows == []

    def test_header_not_on_row4_still_detected(self):
        """Header on row 2 (not row 4) should still be found."""
        content = "title\n\n" + HEADER + "\n" + BUY_ROW
        raw = b"\xef\xbb\xbf" + content.encode("utf-8")
        rows = MiraeParser().parse(raw)
        assert len(rows) == 1

    def test_returns_list_of_trade_rows(self):
        csv_bytes = _make_csv([HEADER, BUY_ROW])
        rows = MiraeParser().parse(csv_bytes)
        assert all(isinstance(r, TradeRow) for r in rows)

    def test_multiple_trades_same_stock_same_day(self):
        """Two buy rows for the same stock on the same day are both returned."""
        row2 = "2026.03.20,유통융자매수입고,삼성전자,500,\"5,500,000\",,750.00,\"1,000,000\""
        csv_bytes = _make_csv([HEADER, BUY_ROW, row2])
        rows = MiraeParser().parse(csv_bytes)
        assert len(rows) == 2

    def test_no_bom_csv_still_parseable(self):
        """UTF-8 without BOM should also work."""
        content = "미래에셋증권 거래내역\n\n\n\n" + HEADER + "\n" + BUY_ROW
        raw = content.encode("utf-8")  # no BOM
        rows = MiraeParser().parse(raw)
        assert len(rows) == 1

    def test_unknown_trade_type_row_skipped(self):
        """A row with a 거래종류 that is neither 매수입고 nor 매도상환 is skipped."""
        unknown_row = "2026.03.20,유통융자대체입고,삼성전자,100,\"1,000,000\",,100.00,\"2,000,000\""
        csv_bytes = _make_csv([HEADER, unknown_row])
        rows = MiraeParser().parse(csv_bytes)
        assert rows == []

    def test_broker_field_is_mirae(self):
        csv_bytes = _make_csv([HEADER, BUY_ROW])
        row = MiraeParser().parse(csv_bytes)[0]
        assert row.broker == "미래에셋"

    def test_malformed_quantity_row_skipped_gracefully(self):
        """A row with a non-numeric quantity value is skipped, not raised."""
        bad_qty_row = "2026.03.20,유통융자매수입고,삼성전자,N/A,\"16,362,500\",,\"2,359.00\",\"1,234,567\""
        csv_bytes = _make_csv([HEADER, bad_qty_row, BUY_ROW])
        rows = MiraeParser().parse(csv_bytes)
        # The bad row is skipped; the good row is still parsed
        assert len(rows) == 1
        assert rows[0].name == "삼성전자"

    def test_parse_quantity_empty_returns_zero(self):
        assert _parse_quantity("") == 0

    def test_parse_quantity_commas_stripped(self):
        assert _parse_quantity("1,500") == 1500

    def test_parse_quantity_invalid_raises_value_error(self):
        with pytest.raises(ValueError, match="Invalid quantity value"):
            _parse_quantity("N/A")


# ---------------------------------------------------------------------------
# TestResolveStockCodes
# ---------------------------------------------------------------------------

class TestResolveStockCodes:
    def _make_trade_row(self, name: str, symbol: str = "") -> TradeRow:
        return TradeRow(
            symbol=symbol,
            name=name,
            side="buy",
            traded_at=datetime(2026, 3, 20),
            price=Decimal("10000"),
            quantity=100,
            amount=Decimal("1000000"),
            broker="미래에셋",
            account_type="유통융자",
            market=None,
            fees=Decimal("150"),
        )

    def test_symbol_filled_when_name_matches(self):
        rows = [self._make_trade_row("삼성전자")]
        result = resolve_stock_codes(rows, {"삼성전자": "005930"})
        assert result[0].symbol == "005930"

    def test_symbol_unchanged_when_no_match(self):
        rows = [self._make_trade_row("현대차")]
        result = resolve_stock_codes(rows, {"삼성전자": "005930"})
        assert result[0].symbol == ""

    def test_multiple_rows_resolved_independently(self):
        rows = [
            self._make_trade_row("삼성전자"),
            self._make_trade_row("현대차"),
            self._make_trade_row("SK하이닉스"),
        ]
        stock_codes = {"삼성전자": "005930", "SK하이닉스": "000660"}
        result = resolve_stock_codes(rows, stock_codes)
        assert result[0].symbol == "005930"
        assert result[1].symbol == ""        # no match
        assert result[2].symbol == "000660"

    def test_empty_rows_returns_empty_list(self):
        result = resolve_stock_codes([], {"삼성전자": "005930"})
        assert result == []

    def test_empty_stock_codes_returns_rows_unchanged(self):
        rows = [self._make_trade_row("삼성전자")]
        result = resolve_stock_codes(rows, {})
        assert result[0].symbol == ""

    def test_original_rows_not_mutated(self):
        """resolve_stock_codes must not modify the original TradeRow objects."""
        original = self._make_trade_row("삼성전자")
        rows = [original]
        resolve_stock_codes(rows, {"삼성전자": "005930"})
        # The original row passed in should be unchanged
        assert original.symbol == ""

    def test_returns_list(self):
        rows = [self._make_trade_row("삼성전자")]
        result = resolve_stock_codes(rows, {"삼성전자": "005930"})
        assert isinstance(result, list)

    def test_existing_symbol_preserved_when_no_match(self):
        """If the row already has a symbol and there is no mapping, keep it."""
        rows = [self._make_trade_row("Unknown Corp", symbol="999999")]
        result = resolve_stock_codes(rows, {"삼성전자": "005930"})
        assert result[0].symbol == "999999"

    def test_mapping_overrides_existing_symbol(self):
        """If a name match exists, the mapping takes precedence."""
        rows = [self._make_trade_row("삼성전자", symbol="000000")]
        result = resolve_stock_codes(rows, {"삼성전자": "005930"})
        assert result[0].symbol == "005930"
