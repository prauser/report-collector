"""CSV parser package for broker trade exports.

Usage
-----
    from trades.csv_parsers import get_parser

    parser = get_parser("mirae")
    rows = parser.parse(file_bytes)
"""
from __future__ import annotations

from trades.csv_parsers.common import BaseBrokerParser, TradeRow, detect_broker, detect_encoding, normalize_stock_code, resolve_stock_codes
from trades.csv_parsers.kiwoom import KiwoomParser
from trades.csv_parsers.mirae import MiraeParser
from trades.csv_parsers.samsung import SamsungParser

__all__ = [
    "BaseBrokerParser",
    "TradeRow",
    "detect_encoding",
    "normalize_stock_code",
    "resolve_stock_codes",
    "get_parser",
    "detect_broker",
    "MiraeParser",
    "KiwoomParser",
    "SamsungParser",
]

_PARSER_MAP: dict[str, type[BaseBrokerParser]] = {
    "mirae": MiraeParser,
    "kiwoom": KiwoomParser,
    "samsung": SamsungParser,
}


def get_parser(broker: str) -> BaseBrokerParser:
    """Return an instantiated parser for the given broker name.

    Parameters
    ----------
    broker:
        Broker identifier (case-insensitive).  Supported values:
        ``"mirae"``, ``"kiwoom"``, ``"samsung"``.

    Raises
    ------
    ValueError
        If the broker is not recognised.
    """
    key = broker.strip().lower()
    cls = _PARSER_MAP.get(key)
    if cls is None:
        supported = ", ".join(sorted(_PARSER_MAP))
        raise ValueError(f"Unknown broker '{broker}'. Supported: {supported}")
    return cls()


