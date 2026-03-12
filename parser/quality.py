"""S2b 추출 결과 품질 판정 — LLM 없이 규칙 기반."""
from parser.base import ParsedReport

# 종목이 없어도 정상인 리포트 타입
_MACRO_TYPES = {"시황/전략", "채권/금리", "공모주", "매매동향", "경제/시황"}


def assess_parse_quality(parsed: ParsedReport) -> str:
    """
    S2b 추출 결과를 보고 파싱 품질을 판정.

    good    — 핵심 필드 충분히 추출됨
    partial — broker/title은 있지만 종목 정보 누락 (재처리 후보)
    poor    — broker 또는 title 자체가 불명확
    """
    broker_ok = bool(parsed.broker and parsed.broker.strip() not in ("", "미상"))
    title_ok = bool(parsed.title and len(parsed.title.strip()) > 5)
    stock_ok = bool(parsed.stock_name or parsed.stock_code)
    macro_type = bool(parsed.report_type and parsed.report_type in _MACRO_TYPES)

    if not title_ok or not broker_ok:
        return "poor"

    if stock_ok or macro_type:
        return "good"

    # broker + title 있지만 종목 정보 없고 매크로 타입도 아님
    return "partial"
