"""@companyreport 채널 전용 파서.

실제 샘플 수집 후 패턴 보완 필요.
기본 구조: GenericParser와 유사하되 채널 특화 패턴 적용.
"""
import re
from datetime import date

from parser.base import BaseParser, ParsedReport
from parser.normalizer import normalize_broker, normalize_opinion, normalize_title, parse_price

PATTERN_STOCK = re.compile(r"([가-힣a-zA-Z]+)\((\d{6})\)")
PATTERN_BROKER = re.compile(
    r"(미래에셋|한국투자|KB|NH|삼성|하나|메리츠|신한|대신|키움|유진|이베스트|교보|흥국|현대차|SK|LS|BNK|한화|DB|IBK|부국)\s*(?:증권|투자증권|금융투자)?"
)
PATTERN_TARGET_PRICE = re.compile(r"목표가[:\s]*([0-9,]+)\s*원")
PATTERN_OPINION = re.compile(r"(매수|중립|매도|비중확대|비중축소|Trading\s*Buy|BUY|HOLD|SELL|Buy|Hold|Sell)")
PATTERN_URL = re.compile(r"https?://\S+")
PATTERN_TME_MSG = re.compile(r"https?://(?:t\.me|telegram\.me)/([a-zA-Z_]\w+)/(\d+)")

_SKIP_HOSTS = {"t.me", "telegram.me"}


def _is_pdf_url(url: str) -> bool:
    """t.me 등 비-PDF 호스트 URL 제외."""
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return host not in _SKIP_HOSTS


class CompanyReportParser(BaseParser):
    CHANNEL = "@companyreport"

    def can_parse(self, channel: str) -> bool:
        return channel.lower() == self.CHANNEL.lower()

    def parse(self, message_text: str, channel: str, message_id: int | None = None) -> ParsedReport | None:
        text = message_text.strip()
        if not text:
            return None

        lines = text.splitlines()
        title = lines[0].strip() if lines else text[:100]

        result = ParsedReport(
            title=title,
            source_channel=channel,
            raw_text=text,
            source_message_id=message_id,
            report_date=None,
        )
        result.title_normalized = normalize_title(title)

        # 종목 추출
        m = PATTERN_STOCK.search(text)
        if m:
            result.stock_name = m.group(1)
            result.stock_code = m.group(2)

        # 증권사 추출
        m_br = PATTERN_BROKER.search(text)
        if m_br:
            result.broker = normalize_broker(m_br.group(0))

        # 목표가
        m_tp = PATTERN_TARGET_PRICE.search(text)
        if m_tp:
            result.target_price = parse_price(m_tp.group(1))

        # 투자의견
        m_op = PATTERN_OPINION.search(text)
        if m_op:
            result.opinion = normalize_opinion(m_op.group(1))

        # URL (t.me 등 비-PDF 호스트 제외)
        for m_url in PATTERN_URL.finditer(text):
            if _is_pdf_url(m_url.group(0)):
                result.pdf_url = m_url.group(0)
                break

        # t.me 메시지 링크 수집 (pdf_url 없을 때 Telethon으로 resolve)
        if not result.pdf_url:
            result.tme_message_links = [m.group(0) for m in PATTERN_TME_MSG.finditer(text)]

        return result
