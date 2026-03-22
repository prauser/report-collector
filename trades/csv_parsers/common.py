"""Common infrastructure for broker CSV parsers."""
from __future__ import annotations

import abc
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass
class TradeRow:
    """Parsed representation of a single trade row from a broker CSV."""

    symbol: str
    name: str
    side: str          # 'buy' or 'sell'
    traded_at: datetime
    price: Decimal
    quantity: int
    amount: Decimal
    broker: str
    account_type: str
    market: str | None
    fees: Decimal | None = field(default=None)


def detect_encoding(file_bytes: bytes) -> str:
    """Detect encoding of CSV bytes.

    Tries UTF-8 with BOM (utf-8-sig) first so that Excel-generated files with
    a BOM are handled transparently.  Then plain UTF-8, then cp949 (EUC-KR
    superset common in Korean broker exports).

    Returns the encoding string suitable for ``bytes.decode()``.
    """
    try:
        file_bytes.decode("utf-8-sig")
        # utf-8-sig succeeds for both BOM-prefixed UTF-8 and plain UTF-8/ASCII,
        # but we only return "utf-8-sig" when a BOM is actually present so that
        # callers get the stripping behaviour they need.
        if file_bytes.startswith(b"\xef\xbb\xbf"):
            return "utf-8-sig"
        return "utf-8"
    except UnicodeDecodeError:
        return "cp949"


def normalize_stock_code(code: str) -> str:
    """Normalize a stock code to a plain 6-digit string.

    Parameters
    ----------
    code:
        Raw stock code string from a CSV cell.

    Raises
    ------
    ValueError
        If *code* is empty or contains only whitespace.

    Examples
    --------
    >>> normalize_stock_code("005930")
    '005930'
    >>> normalize_stock_code("A005930")
    '005930'
    >>> normalize_stock_code("KR005930")
    '005930'
    >>> normalize_stock_code("5930")
    '005930'
    >>> normalize_stock_code("  A005930  ")
    '005930'
    """
    code = code.strip()

    if not code:
        raise ValueError("stock code must not be empty")

    # Strip all leading alphabetic characters (e.g. "A" for Kiwoom, "KR" for
    # some market-data providers)
    while code and code[0].isalpha():
        code = code[1:]

    # Zero-pad to 6 digits
    code = code.zfill(6)

    return code


def detect_broker(file_content: bytes) -> str:
    """Attempt to identify the broker from CSV header patterns.

    Scans the first 10 lines of the decoded file for known broker keywords.
    Mirae Asset CSVs have their distinguishing header on row 4, so we cannot
    rely on the first line alone.

    Parameters
    ----------
    file_content:
        Raw bytes of the uploaded CSV file.

    Returns
    -------
    str
        One of ``"mirae"``, ``"kiwoom"``, ``"samsung"``, or ``"unknown"``.
    """
    encoding = detect_encoding(file_content)
    try:
        text = file_content.decode(encoding)
    except Exception:
        return "unknown"

    lines = text.split("\n")
    first_line = lines[0].lower() if lines else ""
    # Preamble/title lines: first 4 lines only.  Broker names appear in the
    # title area; data rows start after the header (row 4+), so restricting
    # to 4 lines avoids false-positives from stock names like "삼성전자".
    preamble = lines[:4]
    # All first 10 lines are still needed for the structural header fallback.
    first_ten = lines[:10]

    # English broker identifiers are checked only on the first line to avoid
    # false-positives from stock names (e.g. "Samsung" in a trade row).
    if "mirae" in first_line:
        return "mirae"
    if "kiwoom" in first_line:
        return "kiwoom"
    if "samsung" in first_line:
        return "samsung"

    # Korean keywords checked only in the preamble (first 4 lines) to avoid
    # false-positives from stock names such as "삼성전자" in data rows.
    combined_preamble = "\n".join(preamble)
    if "미래에셋" in combined_preamble:
        return "mirae"
    if "키움" in combined_preamble:
        return "kiwoom"
    if "삼성" in combined_preamble:
        return "samsung"

    # Mirae CSV structural pattern: a header row contains both 거래일자 and 거래종류
    for line in first_ten:
        if "거래일자" in line and "거래종류" in line:
            return "mirae"

    # Samsung CSV structural pattern: header contains 거래단가/이율 and 정산금액,
    # which are unique to Samsung Securities exports.
    for line in first_ten:
        if "거래단가/이율" in line and "정산금액" in line:
            return "samsung"

    return "unknown"


def resolve_stock_codes(
    rows: list[TradeRow], stock_codes: dict[str, str]
) -> list[TradeRow]:
    """Fill in TradeRow.symbol from a name→code mapping.

    Parameters
    ----------
    rows:
        List of TradeRow objects, typically from a broker CSV parser.
    stock_codes:
        Dictionary mapping 종목명 (stock name) to 종목코드 (stock code),
        e.g. ``{"삼성전자": "005930"}``.

    Returns
    -------
    list[TradeRow]
        The same rows with ``symbol`` filled in where a match exists.
        Rows with no match in *stock_codes* retain their existing ``symbol``
        value (empty string if unset by the parser).
    """
    result = []
    for row in rows:
        code = stock_codes.get(row.name)
        if code is not None:
            row = dataclasses.replace(row, symbol=code)
        result.append(row)
    return result


class BaseBrokerParser(abc.ABC):
    """Abstract base class for broker-specific CSV parsers."""

    @abc.abstractmethod
    def parse(self, file_content: bytes) -> list[TradeRow]:
        """Parse raw CSV bytes and return a list of TradeRow objects."""
