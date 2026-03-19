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

# 증권사명 매칭 (broker 부분에 사용)
_BROKER_NAMES = (
    r"(?:미래에셋|한국투자|KB|NH|삼성|하나|메리츠|신한|대신|키움|유진|이베스트|교보|흥국|현대차"
    r"|SK|LS|BNK|한화|부국|DB|IBK|유안타|하이|토러스|케이프|신영|한양|동부|리딩)"
    r"(?:증권|투자증권|금융투자)?"
)

# ▶ 종목명(코드) 제목 - 증권사
PATTERN_STOCK_REPORT = re.compile(
    r"[▶►\*]*\s*(.+?)\((\d{6})\)\s*(.+?)\s*[-–]\s*(.+?)(?:\s*[\[<(]|$)",
    re.MULTILINE,
)
# 종목코드 없는 산업/시황 리포트: ▶ 제목 - 증권사
PATTERN_INDUSTRY_REPORT = re.compile(
    r"[▶►\*]*\s*(.+?)\s*[-–]\s*(" + _BROKER_NAMES + r".*?)(?:\s*[\[<(]|$)",
    re.MULTILINE,
)
PATTERN_TARGET_PRICE = re.compile(r"목표가[:\s]*([0-9,]+)\s*원")
PATTERN_PREV_TARGET = re.compile(
    r"목표가[:\s]*([0-9,]+)\s*원?\s*(?:→|->|→)+\s*([0-9,]+)\s*원"
)
PATTERN_OPINION = re.compile(r"목표가[^\(]*\(([^)]+)\)")
PATTERN_OPINION_STANDALONE = re.compile(r"투자의견[:\s]*(매수|중립|매도|비중확대|비중축소|Trading\s*Buy|BUY|HOLD|SELL)")
PATTERN_ANALYST = re.compile(r"[-–]\s*\S+(?:증권|투자증권|금융투자)\s+([가-힣]{2,4})$", re.MULTILINE)
PATTERN_PDF_URL = re.compile(r"https?://\S+\.pdf[^\s)]*", re.IGNORECASE)
PATTERN_URL = re.compile(r"https?://[^\s)\]>]+")
PATTERN_TME_MSG = re.compile(r"https?://(?:t\.me|telegram\.me)/([a-zA-Z_]\w+)/(\d+)")

_SKIP_HOSTS = {"t.me", "telegram.me"}


def _is_pdf_url(url: str) -> bool:
    """t.me 등 비-PDF 호스트 URL 제외."""
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return host not in _SKIP_HOSTS


def _clean_broker(raw: str) -> str:
    """broker 문자열에서 링크/마크다운/불필요 문자 제거 후 정규화."""
    # 링크, 마크다운, 괄호 이후 제거
    raw = re.split(r"[\[<(]", raw)[0].strip()
    # "증권" 뒤의 잡다한 텍스트 제거 (예: "이베스트증권 홍길동" → "이베스트증권")
    m = re.match(r"(.+?(?:증권|투자증권|금융투자))", raw)
    if m:
        raw = m.group(1)
    return normalize_broker(raw.strip())


_DIGEST_SPLIT_RE = re.compile(r'\n+(?=\*?\*?\s*[▶►])')


class RepostoryParser(BaseParser):
    CHANNEL = "@repostory123"

    def can_parse(self, channel: str) -> bool:
        return channel.lower() == self.CHANNEL.lower()

    def parse_multiple(self, message_text: str, channel: str, message_id: int | None = None) -> list[ParsedReport]:
        """다이제스트 메시지를 ▶ 블록 단위로 분리해 개별 ParsedReport 목록 반환."""
        arrow_count = len(re.findall(r'[▶►]', message_text))
        if arrow_count <= 1:
            result = self.parse(message_text, channel, message_id)
            return [result] if result else []

        blocks = _DIGEST_SPLIT_RE.split(message_text)
        results = []
        for block in blocks:
            if not re.search(r'[▶►]', block):
                continue  # 헤더 줄 스킵
            parsed = self.parse(block.strip(), channel, message_id)
            if parsed:
                parsed.raw_text = block.strip()
                results.append(parsed)
        return results

    def parse(self, message_text: str, channel: str, message_id: int | None = None) -> ParsedReport | None:
        text = message_text.strip()
        if not text:
            return None

        # (Continuing...) 이어짐 메시지는 건너뜀
        if text.startswith("(Continuing"):
            return None

        # 마크다운 볼드(**) 제거
        clean = re.sub(r"\*\*", "", text)

        result = ParsedReport(
            title="",
            source_channel=channel,
            raw_text=text,
            source_message_id=message_id,
            report_date=date.today(),
        )

        # 종목 리포트 패턴 시도
        m = PATTERN_STOCK_REPORT.search(clean)
        if m:
            result.stock_name = m.group(1).strip()
            result.stock_code = m.group(2).strip()
            result.title = m.group(3).strip()
            result.broker = _clean_broker(m.group(4))
        else:
            # 산업/시황 리포트
            m2 = PATTERN_INDUSTRY_REPORT.search(clean)
            if m2:
                result.title = m2.group(1).strip()
                result.broker = _clean_broker(m2.group(2))
            else:
                result.parse_errors.append("제목/증권사 파싱 실패")

        if not result.title:
            return None

        result.title_normalized = normalize_title(result.title) if result.title else None

        # 이전 목표가 → 현재 목표가 변경
        m_prev = PATTERN_PREV_TARGET.search(clean)
        if m_prev:
            result.prev_target_price = parse_price(m_prev.group(1))
            result.target_price = parse_price(m_prev.group(2))
        else:
            m_tp = PATTERN_TARGET_PRICE.search(text)
            if m_tp:
                result.target_price = parse_price(m_tp.group(1))

        # 투자의견
        m_op = PATTERN_OPINION.search(clean)
        if m_op:
            result.opinion = normalize_opinion(m_op.group(1).strip())
        else:
            m_op2 = PATTERN_OPINION_STANDALONE.search(clean)
            if m_op2:
                result.opinion = normalize_opinion(m_op2.group(1).strip())

        # 애널리스트
        m_analyst = PATTERN_ANALYST.search(clean)
        if m_analyst:
            result.analyst = m_analyst.group(1).strip()

        # PDF URL (원문 텍스트에서 추출, t.me 등 비-PDF 호스트 제외)
        m_pdf = PATTERN_PDF_URL.search(text)
        if m_pdf and _is_pdf_url(m_pdf.group(0)):
            result.pdf_url = m_pdf.group(0)
        else:
            for m_url in PATTERN_URL.finditer(text):
                if _is_pdf_url(m_url.group(0)):
                    result.pdf_url = m_url.group(0)
                    break

        # t.me 메시지 링크 수집 (pdf_url 없을 때 Telethon으로 resolve)
        if not result.pdf_url:
            result.tme_message_links = [m.group(0) for m in PATTERN_TME_MSG.finditer(text)]

        return result
