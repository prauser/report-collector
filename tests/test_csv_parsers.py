"""Unit tests for trades/csv_parsers infrastructure.

All tests run without a live DB or file system — pure logic only.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime
from decimal import Decimal

import pytest

from trades.csv_parsers.common import (
    BaseBrokerParser,
    TradeRow,
    detect_broker,
    detect_encoding,
    normalize_stock_code,
)
from trades.csv_parsers import detect_broker, get_parser  # noqa: F811 — also importable from package
from trades.csv_parsers.kiwoom import KiwoomParser
from trades.csv_parsers.mirae import MiraeParser
from trades.csv_parsers.samsung import SamsungParser


# ---------------------------------------------------------------------------
# TradeRow dataclass
# ---------------------------------------------------------------------------

class TestTradeRow:
    def _make(self, **overrides):
        defaults = dict(
            symbol="005930",
            name="삼성전자",
            side="buy",
            traded_at=datetime(2024, 1, 15, 9, 30, 0),
            price=Decimal("72000"),
            quantity=10,
            amount=Decimal("720000"),
            broker="mirae",
            account_type="일반",
            market="KOSPI",
        )
        defaults.update(overrides)
        return TradeRow(**defaults)

    def test_basic_fields(self):
        row = self._make()
        assert row.symbol == "005930"
        assert row.name == "삼성전자"
        assert row.side == "buy"
        assert row.price == Decimal("72000")
        assert row.quantity == 10
        assert row.amount == Decimal("720000")
        assert row.broker == "mirae"
        assert row.account_type == "일반"
        assert row.market == "KOSPI"

    def test_fees_defaults_to_none(self):
        row = self._make()
        assert row.fees is None

    def test_fees_can_be_set(self):
        row = self._make(fees=Decimal("360"))
        assert row.fees == Decimal("360")

    def test_is_dataclass(self):
        assert dataclasses.is_dataclass(TradeRow)

    def test_equality(self):
        r1 = self._make()
        r2 = self._make()
        assert r1 == r2

    def test_inequality_on_different_side(self):
        r1 = self._make(side="buy")
        r2 = self._make(side="sell")
        assert r1 != r2


# ---------------------------------------------------------------------------
# detect_encoding
# ---------------------------------------------------------------------------

class TestDetectEncoding:
    def test_utf8_bytes(self):
        data = "삼성전자,005930".encode("utf-8")
        assert detect_encoding(data) == "utf-8"

    def test_ascii_bytes_returns_utf8(self):
        data = b"symbol,name,price\n005930,Samsung,72000"
        assert detect_encoding(data) == "utf-8"

    def test_cp949_bytes(self):
        data = "삼성전자,005930".encode("cp949")
        assert detect_encoding(data) == "cp949"

    def test_empty_bytes(self):
        assert detect_encoding(b"") == "utf-8"

    def test_return_type_is_str(self):
        result = detect_encoding(b"hello")
        assert isinstance(result, str)

    def test_utf8_bom_bytes_returns_utf8_sig(self):
        # Korean Excel exports often prepend a UTF-8 BOM (EF BB BF).
        bom = b"\xef\xbb\xbf"
        data = bom + "삼성전자,005930".encode("utf-8")
        assert detect_encoding(data) == "utf-8-sig"

    def test_utf8_bom_content_is_decodable(self):
        # Decoding with the returned encoding must strip the BOM automatically.
        bom = b"\xef\xbb\xbf"
        payload = "종목,코드\n삼성전자,005930"
        data = bom + payload.encode("utf-8")
        enc = detect_encoding(data)
        decoded = data.decode(enc)
        assert not decoded.startswith("\ufeff"), "BOM character must be stripped"
        assert decoded.startswith("종목")


# ---------------------------------------------------------------------------
# normalize_stock_code
# ---------------------------------------------------------------------------

class TestNormalizeStockCode:
    def test_already_six_digits(self):
        assert normalize_stock_code("005930") == "005930"

    def test_strip_leading_alpha_prefix(self):
        # Kiwoom style
        assert normalize_stock_code("A005930") == "005930"

    def test_zero_pad_short_code(self):
        assert normalize_stock_code("5930") == "005930"

    def test_zero_pad_very_short_code(self):
        assert normalize_stock_code("1") == "000001"

    def test_strip_whitespace(self):
        assert normalize_stock_code("  005930  ") == "005930"

    def test_strip_whitespace_and_prefix(self):
        assert normalize_stock_code("  A005930  ") == "005930"

    def test_six_digit_with_zeros(self):
        assert normalize_stock_code("000660") == "000660"

    def test_short_with_prefix(self):
        # "A5930" -> strip A -> "5930" -> pad -> "005930"
        assert normalize_stock_code("A5930") == "005930"

    def test_kosdaq_style_code(self):
        assert normalize_stock_code("035720") == "035720"

    def test_result_is_string(self):
        result = normalize_stock_code("005930")
        assert isinstance(result, str)

    def test_result_length_is_six(self):
        result = normalize_stock_code("5930")
        assert len(result) == 6

    def test_empty_string_raises_value_error(self):
        with pytest.raises(ValueError):
            normalize_stock_code("")

    def test_whitespace_only_raises_value_error(self):
        with pytest.raises(ValueError):
            normalize_stock_code("   ")

    def test_multi_char_prefix_stripped(self):
        # "KR005930" -> strip "K", "R" -> "005930"
        assert normalize_stock_code("KR005930") == "005930"

    def test_two_char_prefix_with_padding(self):
        # "KR5930" -> strip "K", "R" -> "5930" -> pad -> "005930"
        assert normalize_stock_code("KR5930") == "005930"


# ---------------------------------------------------------------------------
# BaseBrokerParser — ABC enforcement
# ---------------------------------------------------------------------------

class TestBaseBrokerParser:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            BaseBrokerParser()  # type: ignore[abstract]

    def test_concrete_subclass_without_parse_raises(self):
        class BadParser(BaseBrokerParser):
            pass

        with pytest.raises(TypeError):
            BadParser()  # type: ignore[abstract]

    def test_concrete_subclass_with_parse_works(self):
        class GoodParser(BaseBrokerParser):
            def parse(self, file_content: bytes) -> list[TradeRow]:
                return []

        parser = GoodParser()
        assert parser.parse(b"") == []


# ---------------------------------------------------------------------------
# Broker stubs (mirae / kiwoom / samsung)
# ---------------------------------------------------------------------------

class TestBrokerStubs:
    @pytest.mark.parametrize("cls", [KiwoomParser])
    def test_parse_raises_not_implemented(self, cls):
        parser = cls()
        with pytest.raises(NotImplementedError):
            parser.parse(b"some,csv,data")

    @pytest.mark.parametrize("cls", [MiraeParser, SamsungParser])
    def test_parse_does_not_raise_not_implemented(self, cls):
        # Fully implemented parsers return a list for unrecognised input.
        parser = cls()
        result = parser.parse(b"some,csv,data")
        assert isinstance(result, list)

    @pytest.mark.parametrize("cls", [MiraeParser, KiwoomParser, SamsungParser])
    def test_is_subclass_of_base(self, cls):
        assert issubclass(cls, BaseBrokerParser)

    @pytest.mark.parametrize("cls", [MiraeParser, KiwoomParser, SamsungParser])
    def test_instantiation_succeeds(self, cls):
        parser = cls()
        assert parser is not None


# ---------------------------------------------------------------------------
# get_parser factory
# ---------------------------------------------------------------------------

class TestGetParser:
    def test_mirae(self):
        parser = get_parser("mirae")
        assert isinstance(parser, MiraeParser)

    def test_kiwoom(self):
        parser = get_parser("kiwoom")
        assert isinstance(parser, KiwoomParser)

    def test_samsung(self):
        parser = get_parser("samsung")
        assert isinstance(parser, SamsungParser)

    def test_case_insensitive(self):
        assert isinstance(get_parser("Mirae"), MiraeParser)
        assert isinstance(get_parser("KIWOOM"), KiwoomParser)
        assert isinstance(get_parser("Samsung"), SamsungParser)

    def test_leading_trailing_spaces(self):
        assert isinstance(get_parser("  mirae  "), MiraeParser)

    def test_unknown_broker_raises(self):
        with pytest.raises(ValueError, match="Unknown broker"):
            get_parser("nonexistent")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            get_parser("")

    def test_returns_new_instance_each_call(self):
        p1 = get_parser("mirae")
        p2 = get_parser("mirae")
        assert p1 is not p2


# ---------------------------------------------------------------------------
# detect_broker
# ---------------------------------------------------------------------------

class TestDetectBroker:
    def test_unknown_for_generic_csv(self):
        data = b"symbol,name,price\n005930,Samsung,72000"
        assert detect_broker(data) == "unknown"

    def test_detects_mirae_by_english_keyword(self):
        data = "mirae,체결일,종목명,수량".encode("utf-8")
        assert detect_broker(data) == "mirae"

    def test_detects_kiwoom_by_english_keyword(self):
        data = "kiwoom,체결일,종목명,수량".encode("utf-8")
        assert detect_broker(data) == "kiwoom"

    def test_detects_samsung_by_english_keyword(self):
        data = "samsung,체결일,종목명,수량".encode("utf-8")
        assert detect_broker(data) == "samsung"

    def test_detects_mirae_korean_keyword_utf8(self):
        data = "미래에셋,체결일,종목명,수량".encode("utf-8")
        assert detect_broker(data) == "mirae"

    def test_detects_kiwoom_korean_keyword_utf8(self):
        data = "키움,체결일,종목명,수량".encode("utf-8")
        assert detect_broker(data) == "kiwoom"

    def test_detects_samsung_korean_keyword_utf8(self):
        data = "삼성,체결일,종목명,수량".encode("utf-8")
        assert detect_broker(data) == "samsung"

    def test_returns_string(self):
        result = detect_broker(b"hello")
        assert isinstance(result, str)

    def test_detects_mirae_korean_keyword_cp949(self):
        # Simulate a Korean broker CSV exported as CP949 (EUC-KR superset).
        data = "미래에셋,체결일,종목명,수량".encode("cp949")
        assert detect_broker(data) == "mirae"

    def test_detects_kiwoom_korean_keyword_cp949(self):
        data = "키움,체결일,종목명,수량".encode("cp949")
        assert detect_broker(data) == "kiwoom"

    def test_detects_samsung_korean_keyword_cp949(self):
        data = "삼성,체결일,종목명,수량".encode("cp949")
        assert detect_broker(data) == "samsung"

    def test_detect_broker_importable_from_common(self):
        # Verify detect_broker lives in common and is not just a __init__ symbol.
        from trades.csv_parsers.common import detect_broker as db_common
        assert callable(db_common)
        assert db_common(b"unknown,csv") == "unknown"

    def test_structural_heuristic_detects_mirae_without_broker_name(self):
        """A CSV with 거래일자+거래종류 header but no broker name → 'mirae'."""
        # Mirae CSV: 4 preamble lines (no broker name), then header with 거래일자/거래종류
        lines = [
            "거래내역",           # row 0: title, no broker keyword
            "",                   # row 1: empty
            "",                   # row 2: empty
            "",                   # row 3: empty
            "거래일자,거래종류,종목명,거래수량,거래금액,수수료",  # row 4: structural header
        ]
        data = "\n".join(lines).encode("utf-8")
        assert detect_broker(data) == "mirae"

    def test_structural_heuristic_no_false_positive_for_samsung_in_data_row(self):
        """'삼성전자' appearing only in data rows (line 5+) must NOT be detected as samsung."""
        # Mirae-format CSV: preamble has no broker keyword, data row contains 삼성전자
        lines = [
            "거래내역",           # row 0: title
            "",                   # row 1
            "",                   # row 2
            "",                   # row 3
            "거래일자,거래종류,종목명,거래수량,거래금액,수수료",  # row 4: header
            "2026.03.20,유통융자매수입고,삼성전자,1500,16362500,2359",  # row 5: data
        ]
        data = "\n".join(lines).encode("utf-8")
        # The structural heuristic (거래일자+거래종류) fires before 삼성 check in preamble,
        # so result is "mirae" not "samsung"
        assert detect_broker(data) == "mirae"

    def test_korean_keyword_in_preamble_not_in_data_rows(self):
        """Korean '삼성' keyword restricted to preamble (first 4 lines) avoids data-row false positives."""
        # A non-samsung CSV that happens to have a stock named '삼성...' only in row 5
        lines = [
            "거래내역",
            "",
            "",
            "",
            "Date,Type,Name,Qty",            # row 4: generic header (no structural pattern)
            "2026.03.20,buy,삼성전자,100",   # row 5: data row with 삼성
        ]
        data = "\n".join(lines).encode("utf-8")
        # No broker name in preamble, no structural Mirae pattern → unknown
        assert detect_broker(data) == "unknown"
