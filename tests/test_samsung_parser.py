"""Unit tests for SamsungParser and detect_broker Samsung pattern.

All tests run without a live DB or file system — pure logic only.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from trades.csv_parsers.samsung import SamsungParser, _parse_number, _parse_quantity, _parse_date, _resolve_side
from trades.csv_parsers.common import TradeRow, detect_broker


# ---------------------------------------------------------------------------
# CSV builder helpers
# ---------------------------------------------------------------------------

# Standard Samsung CSV header
HEADER = "거래일자,거래명,상품명,거래수량,거래단가/이율,거래금액,정산금액,현금잔액,수수료,잔고수량,평가금액"

# A typical buy row
BUY_ROW = '2026-03-13,운용지시(매수),KODEX 200,100,"52,500","5,250,000","5,250,000","1,000,000",0,100,"5,250,000"'

# A typical sell row
SELL_ROW = '2026-03-14,운용지시(매도),TIGER 미국S&P500,50,"75,200","3,760,000","3,760,000","4,800,000",0,50,"3,760,000"'

# Non-trade row — should be skipped
NON_TRADE_ROW = '2026-03-01,기본부담금,,0,0,"100,000","100,000","900,000",0,0,0'

# Interest row — should also be skipped
INTEREST_ROW = '2026-03-31,이자,,0,0,"500","500","900,500",0,0,0'

# Zero-quantity row with a valid 거래명 — should be skipped
ZERO_QTY_BUY_ROW = '2026-03-15,운용지시(매수),KODEX 200,0,"52,500",0,0,"1,000,000",0,0,0'

# Row with a non-zero fee
FEE_BUY_ROW = '2026-03-16,운용지시(매수),KODEX 200,100,"52,500","5,250,000","5,249,840","1,000,000","160",100,"5,250,000"'

# Cancellation row — "매수취소" contains "매수" as substring but should NOT match
CANCEL_ROW = '2026-03-15,매수취소,KODEX 200,0,"52,500",0,0,"1,000,000",0,0,0'

# Empty row (commas only) — row 1 in real Samsung CSV
EMPTY_ROW = ",,,,,,,,,,"


def _make_csv(rows: list[str], encoding: str = "cp949") -> bytes:
    """Build a Samsung-style CSV bytes object from a list of line strings.

    Samsung CSV structure:
        row 0 — header
        row 1 — empty (commas only)
        row 2+ — data
    """
    all_rows = [HEADER, EMPTY_ROW] + rows
    content = "\n".join(all_rows)
    return content.encode(encoding)


def _make_csv_no_empty(rows: list[str], encoding: str = "cp949") -> bytes:
    """Build a Samsung-style CSV without the empty row separator."""
    all_rows = [HEADER] + rows
    content = "\n".join(all_rows)
    return content.encode(encoding)


# ---------------------------------------------------------------------------
# TestSamsungParserNormal
# ---------------------------------------------------------------------------

class TestSamsungParserNormal:
    def test_buy_row_parsed(self):
        csv_bytes = _make_csv([BUY_ROW])
        parser = SamsungParser()
        rows = parser.parse(csv_bytes)
        assert len(rows) == 1
        row = rows[0]
        assert row.side == "buy"
        assert row.name == "KODEX 200"
        assert row.broker == "삼성"

    def test_sell_row_parsed(self):
        csv_bytes = _make_csv([SELL_ROW])
        parser = SamsungParser()
        rows = parser.parse(csv_bytes)
        assert len(rows) == 1
        assert rows[0].side == "sell"

    def test_non_trade_row_skipped(self):
        csv_bytes = _make_csv([NON_TRADE_ROW])
        parser = SamsungParser()
        rows = parser.parse(csv_bytes)
        assert rows == []

    def test_interest_row_skipped(self):
        csv_bytes = _make_csv([INTEREST_ROW])
        parser = SamsungParser()
        rows = parser.parse(csv_bytes)
        assert rows == []

    def test_empty_row_skipped(self):
        csv_bytes = _make_csv([EMPTY_ROW, BUY_ROW])
        parser = SamsungParser()
        rows = parser.parse(csv_bytes)
        assert len(rows) == 1

    def test_mixed_rows(self):
        """Buy + sell + non-trade rows → only 2 trade rows returned."""
        csv_bytes = _make_csv([BUY_ROW, NON_TRADE_ROW, SELL_ROW, INTEREST_ROW])
        rows = SamsungParser().parse(csv_bytes)
        assert len(rows) == 2
        assert rows[0].side == "buy"
        assert rows[1].side == "sell"

    def test_quantity_parsed_with_commas(self):
        row_str = '2026-03-13,운용지시(매수),KODEX 200,"1,500","52,500","78,750,000","78,750,000","1,000,000",0,"1,500","78,750,000"'
        csv_bytes = _make_csv([row_str])
        row = SamsungParser().parse(csv_bytes)[0]
        assert row.quantity == 1500

    def test_amount_parsed_with_commas(self):
        csv_bytes = _make_csv([BUY_ROW])
        row = SamsungParser().parse(csv_bytes)[0]
        assert row.amount == Decimal("5250000")

    def test_fees_zero(self):
        """Retirement account trades typically have zero fees."""
        csv_bytes = _make_csv([BUY_ROW])
        row = SamsungParser().parse(csv_bytes)[0]
        assert row.fees == Decimal("0")

    def test_fees_nonzero(self):
        """Rows with a non-zero fee value have fees parsed correctly."""
        csv_bytes = _make_csv([FEE_BUY_ROW])
        row = SamsungParser().parse(csv_bytes)[0]
        assert row.fees == Decimal("160")

    def test_zero_quantity_row_skipped(self):
        """A row with 거래수량=0 but valid 거래명 is skipped."""
        csv_bytes = _make_csv([ZERO_QTY_BUY_ROW, BUY_ROW])
        rows = SamsungParser().parse(csv_bytes)
        assert len(rows) == 1
        assert rows[0].quantity == 100

    def test_cancel_row_skipped(self):
        """A row with 매수취소 in 거래명 is NOT treated as a buy."""
        csv_bytes = _make_csv([CANCEL_ROW])
        rows = SamsungParser().parse(csv_bytes)
        assert rows == []

    def test_price_from_dedicated_column(self):
        """Samsung has a separate price column (거래단가/이율)."""
        csv_bytes = _make_csv([BUY_ROW])
        row = SamsungParser().parse(csv_bytes)[0]
        assert row.price == Decimal("52500")

    def test_traded_at_is_datetime(self):
        csv_bytes = _make_csv([BUY_ROW])
        row = SamsungParser().parse(csv_bytes)[0]
        assert isinstance(row.traded_at, datetime)
        assert row.traded_at == datetime(2026, 3, 13, 0, 0, 0)

    def test_symbol_is_empty_string(self):
        csv_bytes = _make_csv([BUY_ROW])
        row = SamsungParser().parse(csv_bytes)[0]
        assert row.symbol == ""

    def test_market_is_none(self):
        csv_bytes = _make_csv([BUY_ROW])
        row = SamsungParser().parse(csv_bytes)[0]
        assert row.market is None

    def test_returns_list_of_trade_rows(self):
        csv_bytes = _make_csv([BUY_ROW])
        rows = SamsungParser().parse(csv_bytes)
        assert all(isinstance(r, TradeRow) for r in rows)


# ---------------------------------------------------------------------------
# TestSamsungParserAccountType
# ---------------------------------------------------------------------------

class TestSamsungParserAccountType:
    def test_account_type_is_retirement(self):
        """운용지시 → 퇴직연금 account type."""
        csv_bytes = _make_csv([BUY_ROW])
        row = SamsungParser().parse(csv_bytes)[0]
        assert row.account_type == "퇴직연금"

    def test_sell_row_account_type_is_retirement(self):
        csv_bytes = _make_csv([SELL_ROW])
        row = SamsungParser().parse(csv_bytes)[0]
        assert row.account_type == "퇴직연금"


# ---------------------------------------------------------------------------
# TestSamsungParserEncoding
# ---------------------------------------------------------------------------

class TestSamsungParserEncoding:
    def test_cp949_encoded_csv_parsed(self):
        """CP949-encoded file should be decoded and parsed correctly."""
        csv_bytes = _make_csv([BUY_ROW], encoding="cp949")
        rows = SamsungParser().parse(csv_bytes)
        assert len(rows) == 1
        assert rows[0].name == "KODEX 200"

    def test_korean_stock_name_cp949(self):
        """Korean-named ETF in CP949 encoding is parsed correctly."""
        row_str = '2026-03-13,운용지시(매수),삼성전자,100,"52,500","5,250,000","5,250,000","1,000,000",0,100,"5,250,000"'
        csv_bytes = _make_csv([row_str], encoding="cp949")
        rows = SamsungParser().parse(csv_bytes)
        assert len(rows) == 1
        assert rows[0].name == "삼성전자"


# ---------------------------------------------------------------------------
# TestSamsungParserEdgeCases
# ---------------------------------------------------------------------------

class TestSamsungParserEdgeCases:
    def test_empty_bytes_returns_empty_list(self):
        rows = SamsungParser().parse(b"")
        assert rows == []

    def test_header_only_returns_empty_list(self):
        csv_bytes = HEADER.encode("cp949")
        rows = SamsungParser().parse(csv_bytes)
        assert rows == []

    def test_no_header_row_returns_empty_list(self):
        raw = "col1,col2,col3\nval1,val2,val3\n".encode("cp949")
        rows = SamsungParser().parse(raw)
        assert rows == []

    def test_malformed_quantity_row_skipped(self):
        """A row with a non-numeric quantity value is skipped, not raised."""
        bad_row = '2026-03-13,운용지시(매수),KODEX 200,N/A,"52,500","5,250,000","5,250,000","1,000,000",0,100,"5,250,000"'
        csv_bytes = _make_csv([bad_row, BUY_ROW])
        rows = SamsungParser().parse(csv_bytes)
        assert len(rows) == 1
        assert rows[0].name == "KODEX 200"

    def test_bad_date_row_skipped(self):
        """A row with an unparseable date is skipped."""
        bad_date_row = '20260313,운용지시(매수),KODEX 200,100,"52,500","5,250,000","5,250,000","1,000,000",0,100,"5,250,000"'
        csv_bytes = _make_csv([bad_date_row, BUY_ROW])
        rows = SamsungParser().parse(csv_bytes)
        assert len(rows) == 1

    def test_multiple_trades_same_stock_same_day(self):
        row2 = '2026-03-13,운용지시(매수),KODEX 200,50,"52,500","2,625,000","2,625,000","500,000",0,150,"7,875,000"'
        csv_bytes = _make_csv([BUY_ROW, row2])
        rows = SamsungParser().parse(csv_bytes)
        assert len(rows) == 2

    def test_no_empty_separator_row_still_works(self):
        """CSV without the empty separator row 1 is still parseable."""
        csv_bytes = _make_csv_no_empty([BUY_ROW])
        rows = SamsungParser().parse(csv_bytes)
        assert len(rows) == 1

    def test_broker_field_is_samsung(self):
        csv_bytes = _make_csv([BUY_ROW])
        row = SamsungParser().parse(csv_bytes)[0]
        assert row.broker == "삼성"


# ---------------------------------------------------------------------------
# TestDetectBrokerSamsungPattern
# ---------------------------------------------------------------------------

class TestDetectBrokerSamsungPattern:
    def test_samsung_structural_detection(self):
        """detect_broker identifies Samsung by 거래단가/이율 + 정산금액 in header."""
        csv_bytes = _make_csv([BUY_ROW])
        assert detect_broker(csv_bytes) == "samsung"

    def test_samsung_header_only(self):
        """Header-only Samsung CSV is still detected."""
        csv_bytes = HEADER.encode("cp949")
        assert detect_broker(csv_bytes) == "samsung"

    def test_samsung_not_confused_with_mirae(self):
        """Samsung CSV does not have 거래종류 — should not be identified as Mirae."""
        csv_bytes = _make_csv([BUY_ROW])
        assert detect_broker(csv_bytes) != "mirae"


# ---------------------------------------------------------------------------
# TestHelperFunctions
# ---------------------------------------------------------------------------

class TestHelperFunctions:
    def test_parse_quantity_commas_stripped(self):
        assert _parse_quantity("1,500") == 1500

    def test_parse_quantity_empty_returns_zero(self):
        assert _parse_quantity("") == 0

    def test_parse_quantity_invalid_raises(self):
        with pytest.raises(ValueError, match="Invalid quantity value"):
            _parse_quantity("N/A")

    def test_parse_date_hyphen_format(self):
        result = _parse_date("2026-03-13")
        assert result == datetime(2026, 3, 13, 0, 0, 0)

    def test_parse_date_with_whitespace(self):
        result = _parse_date("  2026-03-13  ")
        assert result == datetime(2026, 3, 13, 0, 0, 0)

    def test_parse_date_invalid_raises(self):
        with pytest.raises(ValueError):
            _parse_date("2026.03.13")

    def test_resolve_side_buy(self):
        assert _resolve_side("운용지시(매수)") == "buy"

    def test_resolve_side_sell(self):
        assert _resolve_side("운용지시(매도)") == "sell"

    def test_resolve_side_non_trade(self):
        assert _resolve_side("기본부담금") is None

    def test_resolve_side_interest(self):
        assert _resolve_side("이자") is None

    def test_resolve_side_cancel_returns_none(self):
        """매수취소 contains bare '매수' but should NOT match — returns None."""
        assert _resolve_side("매수취소") is None

    def test_parse_number_commas_stripped(self):
        assert _parse_number("1,234,567") == Decimal("1234567")

    def test_parse_number_empty_returns_zero(self):
        assert _parse_number("") == Decimal("0")

    def test_parse_number_whitespace_only_returns_zero(self):
        assert _parse_number("   ") == Decimal("0")

    def test_parse_number_invalid_raises(self):
        from decimal import InvalidOperation
        with pytest.raises(InvalidOperation):
            _parse_number("abc")
