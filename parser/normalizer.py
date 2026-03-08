import re

# 증권사명 정규화 매핑
BROKER_ALIASES: dict[str, str] = {
    "미래에셋": "미래에셋증권",
    "미래에셋대우": "미래에셋증권",
    "한투": "한국투자증권",
    "한국투자": "한국투자증권",
    "KB": "KB증권",
    "KB금융": "KB증권",
    "NH": "NH투자증권",
    "NH투자": "NH투자증권",
    "삼성": "삼성증권",
    "하나": "하나증권",
    "하나금융": "하나증권",
    "메리츠": "메리츠증권",
    "신한": "신한투자증권",
    "신한금융": "신한투자증권",
    "대신": "대신증권",
    "키움": "키움증권",
    "유진": "유진투자증권",
    "이베스트": "이베스트투자증권",
    "교보": "교보증권",
    "흥국": "흥국증권",
    "현대차": "현대차증권",
    "SK": "SK증권",
    "LS": "LS증권",
    "BNK": "BNK투자증권",
    "한화": "한화투자증권",
    "부국": "부국증권",
    "DB": "DB금융투자",
    "IBK": "IBK투자증권",
}

OPINION_ALIASES: dict[str, str] = {
    "BUY": "매수",
    "buy": "매수",
    "Buy": "매수",
    "강력매수": "매수",
    "HOLD": "중립",
    "hold": "중립",
    "Hold": "중립",
    "보유": "중립",
    "SELL": "매도",
    "sell": "매도",
    "Sell": "매도",
    "비중확대": "비중확대",
    "Overweight": "비중확대",
    "OW": "비중확대",
    "비중축소": "비중축소",
    "Underweight": "비중축소",
    "UW": "비중축소",
    "Trading Buy": "Trading Buy",
    "시장수익률": "중립",
    "시장수익률상회": "비중확대",
    "시장수익률하회": "비중축소",
}


def normalize_broker(name: str) -> str:
    """증권사명 정규화."""
    name = name.strip()
    return BROKER_ALIASES.get(name, name)


def normalize_opinion(opinion: str) -> str:
    """투자의견 정규화."""
    opinion = opinion.strip()
    return OPINION_ALIASES.get(opinion, opinion)


def normalize_title(title: str) -> str:
    """제목 정규화 - 중복 체크용 (한글+영숫자만 남기고 소문자화)."""
    return re.sub(r"[^가-힣a-zA-Z0-9]", "", title).lower()


def normalize_stock_name(name: str) -> str:
    """종목명 정규화 - 비교용."""
    return re.sub(r"[\s\(\)（）㈜·\-]", "", name).strip()


def parse_price(text: str) -> int | None:
    """'85,000원', '85000', '8.5만' 등을 정수로 변환."""
    text = text.replace(",", "").replace(" ", "")
    match = re.search(r"(\d+(?:\.\d+)?)(만|억)?", text)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2)
    if unit == "만":
        value *= 10_000
    elif unit == "억":
        value *= 100_000_000
    return int(value)
