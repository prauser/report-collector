"""Mirae Asset (미래에셋증권) broker CSV parser."""
from __future__ import annotations

import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation

from trades.csv_parsers.common import BaseBrokerParser, TradeRow, detect_encoding

# Expected header columns in the Mirae CSV
_MIRAE_HEADER_START = "거래일자"

_SIDE_MAP = {
    "매수입고": "buy",
    "매도상환": "sell",
}


def _parse_number(value: str) -> Decimal:
    """Strip commas and convert to Decimal."""
    cleaned = value.replace(",", "").strip()
    if not cleaned:
        return Decimal("0")
    return Decimal(cleaned)


def _parse_quantity(value: str) -> int:
    """Strip commas and convert to int.

    Raises
    ------
    ValueError
        If the cleaned value is not a valid integer string.
    """
    cleaned = value.replace(",", "").strip()
    if not cleaned:
        return 0
    try:
        return int(cleaned)
    except ValueError:
        raise ValueError(f"Invalid quantity value: {value!r}")


def _parse_date(value: str) -> datetime:
    """Parse '2026.03.20' style date to datetime at 00:00:00."""
    return datetime.strptime(value.strip(), "%Y.%m.%d")


def _resolve_side(trade_type: str) -> str | None:
    """Return 'buy' or 'sell' based on the 거래종류 field, or None to skip."""
    for keyword, side in _SIDE_MAP.items():
        if keyword in trade_type:
            return side
    return None


def _resolve_account_type(trade_type: str) -> str:
    """Extract account_type prefix from 거래종류 (자기융자 / 유통융자)."""
    if "자기융자" in trade_type:
        return "자기융자"
    if "유통융자" in trade_type:
        return "유통융자"
    return trade_type


class MiraeParser(BaseBrokerParser):
    """Parser for Mirae Asset (미래에셋증권) trade export CSV.

    CSV structure
    -------------
    - Encoding: UTF-8 BOM (utf-8-sig)
    - Rows 0-3: title / empty (skipped)
    - Row 4 (variable): header — 거래일자,거래종류,종목명,거래수량,거래금액,외화거래금액,수수료,예수금잔고
    - Remaining rows: data

    The header row is detected dynamically by scanning for the first row
    whose first column is "거래일자".
    """

    def parse(self, file_content: bytes) -> list[TradeRow]:
        """Parse raw CSV bytes and return a list of TradeRow objects.

        Rows where 종목명 is empty or 거래수량 is 0 are skipped (대금입출금).
        Rows whose 거래종류 does not contain a recognised side keyword are also
        skipped.
        """
        if not file_content:
            return []

        encoding = detect_encoding(file_content)
        text = file_content.decode(encoding)

        reader = csv.reader(io.StringIO(text))
        rows = list(reader)

        # Find the header row dynamically
        header_idx = None
        for i, row in enumerate(rows):
            if row and row[0].strip() == _MIRAE_HEADER_START:
                header_idx = i
                break

        if header_idx is None:
            return []

        header = [col.strip() for col in rows[header_idx]]

        try:
            idx_date = header.index("거래일자")
            idx_type = header.index("거래종류")
            idx_name = header.index("종목명")
            idx_qty = header.index("거래수량")
            idx_amount = header.index("거래금액")
            idx_fee = header.index("수수료")
        except ValueError:
            # Missing a required column — cannot parse
            return []

        result: list[TradeRow] = []

        for row in rows[header_idx + 1 :]:
            if not row or all(cell.strip() == "" for cell in row):
                continue

            # Pad short rows
            while len(row) <= max(idx_date, idx_type, idx_name, idx_qty, idx_amount, idx_fee):
                row.append("")

            name = row[idx_name].strip()
            qty_str = row[idx_qty].strip()
            try:
                quantity = _parse_quantity(qty_str)
            except ValueError:
                continue

            # Skip fund-transfer rows (대금입출금): empty name or zero quantity
            if not name or quantity == 0:
                continue

            trade_type = row[idx_type].strip()
            side = _resolve_side(trade_type)
            if side is None:
                continue

            date_str = row[idx_date].strip()
            try:
                traded_at = _parse_date(date_str)
            except ValueError:
                continue

            try:
                amount = _parse_number(row[idx_amount])
            except InvalidOperation:
                amount = Decimal("0")

            try:
                fees = _parse_number(row[idx_fee])
            except InvalidOperation:
                fees = Decimal("0")

            # Calculate unit price from amount / quantity
            if quantity > 0:
                price = (amount / Decimal(quantity)).quantize(Decimal("0.01"))
            else:
                price = Decimal("0")

            account_type = _resolve_account_type(trade_type)

            result.append(
                TradeRow(
                    symbol="",
                    name=name,
                    side=side,
                    traded_at=traded_at,
                    price=price,
                    quantity=quantity,
                    amount=amount,
                    broker="미래에셋",
                    account_type=account_type,
                    market=None,
                    fees=fees,
                )
            )

        return result
