"""@repostory123 채널 전용 파서.

메시지 형식 예시:
▶ 삼성전자(005930) 반도체 업황 개선 지속 - 미래에셋증권
<원문 Link>
- 목표가: 85,000원 (매수)
- HBM3E 양산 본격화로 메모리 실적 개선 전망
"""
import re
from datetime import date

from parser.base import BaseParser, ParsedReport
from parser.normalizer import normalize_broker, normalize_opinion, normalize_title, parse_price

# ▶ 종목명(코드) 제목 - 증권사
PATTERN_STOCK_REPORT = re.compile(
    r"[▶►]\s*(.+?)\((\d{6})\)\s+(.+?)\s*[-–]\s*(.+?)$",
    re.MULTILINE,
)
# 종목코드 없는 산업/시황 리포트: ▶ [산업] 제목 - 증권사
PATTERN_INDUSTRY_REPORT = re.compile(
    r"[▶►]\s*(.+?)\s*[-–]\s*(.+?)$",
    re.MULTILINE,
)
PATTERN_TARGET_PRICE = re.compile(r"목표가[:\s]*([0-9,]+)\s*원")
PATTERN_OPINION = re.compile(r"목표가[^\(]*\(([^)]+)\)")
PATTERN_PDF_URL = re.compile(r"https?://\S+\.pdf\b", re.IGNORECASE)
PATTERN_URL = re.compile(r"https?://\S+")


class RepostoryParser(BaseParser):
    CHANNEL = "@repostory123"

    def can_parse(self, channel: str) -> bool:
        return channel.lower() == self.CHANNEL.lower()

    def parse(self, message_text: str, channel: str, message_id: int | None = None) -> ParsedReport | None:
        text = message_text.strip()
        if not text:
            return None

        result = ParsedReport(
            title="",
            source_channel=channel,
            raw_text=text,
            source_message_id=message_id,
            report_date=date.today(),
        )

        # 종목 리포트 패턴 시도
        m = PATTERN_STOCK_REPORT.search(text)
        if m:
            result.stock_name = m.group(1).strip()
            result.stock_code = m.group(2).strip()
            result.title = m.group(3).strip()
            result.broker = normalize_broker(m.group(4).strip())
        else:
            # 산업/시황 리포트
            m2 = PATTERN_INDUSTRY_REPORT.search(text)
            if m2:
                result.title = m2.group(1).strip()
                result.broker = normalize_broker(m2.group(2).strip())
            else:
                result.parse_errors.append("제목/증권사 파싱 실패")

        if not result.title:
            return None

        result.title_normalized = normalize_title(result.title) if result.title else None

        # 목표가
        m_tp = PATTERN_TARGET_PRICE.search(text)
        if m_tp:
            result.target_price = parse_price(m_tp.group(1))

        # 투자의견
        m_op = PATTERN_OPINION.search(text)
        if m_op:
            result.opinion = normalize_opinion(m_op.group(1).strip())

        # PDF URL
        m_pdf = PATTERN_PDF_URL.search(text)
        if m_pdf:
            result.pdf_url = m_pdf.group(0)
        else:
            m_url = PATTERN_URL.search(text)
            if m_url:
                result.pdf_url = m_url.group(0)

        return result
