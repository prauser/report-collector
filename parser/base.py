from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date


@dataclass
class ParsedReport:
    """파싱 결과 데이터 클래스."""
    title: str
    source_channel: str
    raw_text: str

    broker: str | None = None
    report_date: date | None = None
    analyst: str | None = None
    stock_name: str | None = None
    title_normalized: str | None = None
    stock_code: str | None = None
    sector: str | None = None
    report_type: str | None = None

    opinion: str | None = None
    target_price: int | None = None
    prev_opinion: str | None = None
    prev_target_price: int | None = None

    earnings_quarter: str | None = None
    est_revenue: int | None = None
    est_op_profit: int | None = None
    est_eps: int | None = None
    earnings_surprise: str | None = None

    pdf_url: str | None = None
    tme_message_links: list[str] = field(default_factory=list)
    source_message_id: int | None = None

    parse_quality: str | None = None  # good / partial / poor
    parse_errors: list[str] = field(default_factory=list)


class BaseParser(ABC):
    """채널별 파서의 공통 인터페이스."""

    @abstractmethod
    def can_parse(self, channel: str) -> bool:
        """이 파서가 해당 채널을 처리할 수 있는지."""
        ...

    @abstractmethod
    def parse(self, message_text: str, channel: str, message_id: int | None = None) -> ParsedReport | None:
        """메시지 텍스트를 파싱하여 ParsedReport 반환. 파싱 불가 시 None."""
        ...

    def extract_broker(self, text: str) -> str | None:
        return None

    def extract_stock(self, text: str) -> tuple[str | None, str | None]:
        """(종목명, 종목코드) 튜플 반환."""
        return None, None

    def extract_analyst(self, text: str) -> str | None:
        return None

    def extract_opinion(self, text: str) -> str | None:
        return None

    def extract_target_price(self, text: str) -> int | None:
        return None

    def extract_report_type(self, text: str) -> str | None:
        return None

    def extract_pdf_url(self, text: str) -> str | None:
        return None
