"""Shared utility: Layer2 메타데이터 적용 + 문자열 잘라내기 헬퍼.

collector/listener.py, run_analysis.py, collector/backfill.py,
scripts/recover_batches.py 등 여러 파일에서 공통으로 사용.
"""
from parser.normalizer import normalize_broker, normalize_opinion, parse_price


def trunc(s: str | None, maxlen: int) -> str | None:
    """문자열 s를 maxlen 이하로 잘라 반환. None 또는 비문자열은 그대로 반환."""
    if isinstance(s, str) and len(s) > maxlen:
        return s[:maxlen]
    return s


def apply_layer2_meta(report, meta: dict) -> dict:
    """
    Layer2 메타데이터로 report 필드 업데이트 값 dict 반환.
    실제 UPDATE는 호출자가 수행.
    """
    if not meta:
        return {}

    updates = {}

    def _pick(key, normalizer=None, maxlen=None):
        val = meta.get(key)
        if val:
            val = normalizer(val) if normalizer else val
            return trunc(val, maxlen) if maxlen and isinstance(val, str) else val
        return None

    if v := _pick("broker", normalize_broker, 50):
        updates["broker"] = v
    if v := _pick("stock_name", maxlen=100):
        updates["stock_name"] = v
    if v := _pick("stock_code"):
        updates["stock_code"] = v
    if v := _pick("analyst", maxlen=100):
        updates["analyst"] = v
    if v := _pick("opinion", normalize_opinion, 20):
        updates["opinion"] = v
    if v := _pick("sector", maxlen=100):
        updates["sector"] = v
    if v := _pick("report_type", maxlen=50):
        updates["report_type"] = v
    if v := _pick("prev_opinion", normalize_opinion, 20):
        updates["prev_opinion"] = v

    tp = meta.get("target_price")
    if isinstance(tp, int) and tp > 0:
        updates["target_price"] = tp
    elif isinstance(tp, str):
        parsed_tp = parse_price(tp)
        if parsed_tp:
            updates["target_price"] = parsed_tp

    ptp = meta.get("prev_target_price")
    if isinstance(ptp, int) and ptp > 0:
        updates["prev_target_price"] = ptp
    elif isinstance(ptp, str):
        parsed_ptp = parse_price(ptp)
        if parsed_ptp:
            updates["prev_target_price"] = parsed_ptp

    return updates


def apply_key_data_meta(key_data, parsed_date=None) -> dict:
    """
    key_data 추출 결과로 report 필드 업데이트 값 dict 반환.
    broker/opinion에 normalize_broker/normalize_opinion 적용.
    실제 UPDATE는 호출자가 수행.

    Args:
        key_data: KeyData 객체 (key_data_extractor 반환값)
        parsed_date: datetime.date 또는 None (key_data.date 파싱 결과)

    Returns:
        {column: value} dict (falsy 값은 제외)
    """
    if not key_data:
        return {}

    raw = {
        "broker": trunc(normalize_broker(key_data.broker), 50) if key_data.broker else None,
        "analyst": trunc(key_data.analyst, 100) if key_data.analyst else None,
        "stock_name": trunc(key_data.stock_name, 100) if key_data.stock_name else None,
        "stock_code": key_data.stock_code or None,
        "opinion": trunc(normalize_opinion(key_data.opinion), 20) if key_data.opinion else None,
        "target_price": key_data.target_price or None,
        "report_type": trunc(key_data.report_type, 50) if key_data.report_type else None,
        "title": trunc(key_data.title, 500) if key_data.title else None,
        "report_date": parsed_date,
    }
    return {k: v for k, v in raw.items() if v}
