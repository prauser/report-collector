"""Samsung Securities (삼성증권) broker CSV parser."""
from __future__ import annotations

import csv
import io
from datetime import datetime
from decimal import Decimal, InvalidOperation

from trades.csv_parsers.common import BaseBrokerParser, TradeRow, detect_encoding

# Expected first column of the Samsung CSV header row
_SAMSUNG_HEADER_START = "거래일자"

# Samsung CSV has "매수" or "매도" embedded in 거래명 for trade rows
# e.g. "운용지시(매수)" → buy, "운용지시(매도)" → sell


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
    """Parse '2026-03-13' style date (hyphen-separated) to datetime at 00:00:00."""
    return datetime.strptime(value.strip(), "%Y-%m-%d")


def _resolve_side(trade_name: str) -> str | None:
    """Return 'buy' or 'sell' based on 거래명, or None to skip non-trade rows.

    Matches "(매수)" and "(매도)" precisely to avoid false positives on
    cancellation rows like "매수취소" that contain the bare substring.
    """
    if "(매수)" in trade_name:
        return "buy"
    if "(매도)" in trade_name:
        return "sell"
    return None


class SamsungParser(BaseBrokerParser):
    """Parser for Samsung Securities (삼성증권) trade export CSV.

    CSV structure
    -------------
    - Encoding: CP949
    - Row 0: header — 거래일자,거래명,상품명,거래수량,거래단가/이율,거래금액,정산금액,현금잔액,수수료,잔고수량,평가금액
    - Row 1: empty (commas only) — skipped
    - Row 2+: data rows

    The header row is detected dynamically by scanning for the first row
    whose first column is "거래일자".
    """

    def parse(self, file_content: bytes) -> list[TradeRow]:
        """Parse raw CSV bytes and return a list of TradeRow objects.

        Rows where 거래명 does not contain "매수" or "매도" are skipped
        (e.g., 기본부담금, 이자).
        Empty rows are also skipped.
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
            if row and row[0].strip() == _SAMSUNG_HEADER_START:
                header_idx = i
                break

        if header_idx is None:
            return []

        header = [col.strip() for col in rows[header_idx]]

        try:
            idx_date = header.index("거래일자")
            idx_trade_name = header.index("거래명")
            idx_name = header.index("상품명")
            idx_qty = header.index("거래수량")
            idx_price = header.index("거래단가/이율")
            idx_amount = header.index("거래금액")
            idx_fee = header.index("수수료")
        except ValueError:
            # Missing a required column — cannot parse
            return []

        result: list[TradeRow] = []

        for row in rows[header_idx + 1:]:
            # Skip empty rows
            if not row or all(cell.strip() == "" for cell in row):
                continue

            # Pad short rows to avoid index errors
            max_idx = max(idx_date, idx_trade_name, idx_name, idx_qty,
                          idx_price, idx_amount, idx_fee)
            while len(row) <= max_idx:
                row.append("")

            trade_name = row[idx_trade_name].strip()
            side = _resolve_side(trade_name)
            if side is None:
                continue

            date_str = row[idx_date].strip()
            try:
                traded_at = _parse_date(date_str)
            except ValueError:
                continue

            name = row[idx_name].strip()

            try:
                quantity = _parse_quantity(row[idx_qty])
            except ValueError:
                continue

            # Skip zero-quantity rows (e.g. administrative entries)
            if quantity == 0:
                continue

            try:
                price = _parse_number(row[idx_price])
            except InvalidOperation:
                price = Decimal("0")

            try:
                amount = _parse_number(row[idx_amount])
            except InvalidOperation:
                amount = Decimal("0")

            try:
                fees = _parse_number(row[idx_fee])
            except InvalidOperation:
                fees = Decimal("0")

            result.append(
                TradeRow(
                    symbol="",
                    name=name,
                    side=side,
                    traded_at=traded_at,
                    price=price,
                    quantity=quantity,
                    amount=amount,
                    broker="삼성",
                    account_type="퇴직연금",
                    market=None,
                    fees=fees,
                )
            )

        return result
