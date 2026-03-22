"""Kiwoom Securities broker CSV parser (stub — awaiting CSV sample)."""
from __future__ import annotations

from trades.csv_parsers.common import BaseBrokerParser, TradeRow


class KiwoomParser(BaseBrokerParser):
    """Parser for Kiwoom Securities (키움증권) trade export CSV."""

    def parse(self, file_content: bytes) -> list[TradeRow]:
        raise NotImplementedError("CSV 샘플 확보 후 구현")
