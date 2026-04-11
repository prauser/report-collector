"""Layer 2 LLM 결과 검증 및 보정 모듈.

LLM tool_input dict가 DB에 저장되기 전에 스키마 검증 + 자동 보정을 수행.
- 보정 가능한 오류: 자동 수정 후 통과
- 구조적 오류: None 반환 (reject)

Correction log: logs/layer2_sanitized.csv
Rejection log:  logs/layer2_validation_failures.csv
"""
from __future__ import annotations

import csv
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

_CSV_LOCK = threading.Lock()

_LOGS_DIR = Path(__file__).parent.parent / "logs"

_SANITIZED_CSV = _LOGS_DIR / "layer2_sanitized.csv"
_FAILURES_CSV = _LOGS_DIR / "layer2_validation_failures.csv"

# Valid enum values
_VALID_CATEGORIES = {"stock", "industry", "macro"}
_CATEGORY_MAP = {
    # Korean → English
    "경제": "macro",
    "거시": "macro",
    "거시경제": "macro",
    "종목": "stock",
    "기업": "stock",
    "산업": "industry",
    "섹터": "industry",
}
_VALID_QUALITY = {"high", "medium", "low", "truncated"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _append_sanitized(report_id: Any, field: str, original: Any, corrected: Any) -> None:
    """보정 내역을 CSV에 기록."""
    try:
        _LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with _CSV_LOCK:
            write_header = not _SANITIZED_CSV.exists()
            with open(_SANITIZED_CSV, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if write_header:
                    writer.writerow(["report_id", "field", "original_value", "corrected_value", "timestamp"])
                writer.writerow([
                    report_id,
                    field,
                    json.dumps(original, ensure_ascii=False) if not isinstance(original, str) else original,
                    json.dumps(corrected, ensure_ascii=False) if not isinstance(corrected, str) else corrected,
                    _now_iso(),
                ])
    except OSError as e:
        log.warning("layer2_sanitized_log_failed", error=str(e))


def _append_failure(report_id: Any, reason: str) -> None:
    """reject 사유를 CSV에 기록."""
    try:
        _LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with _CSV_LOCK:
            write_header = not _FAILURES_CSV.exists()
            with open(_FAILURES_CSV, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                if write_header:
                    writer.writerow(["report_id", "reason", "timestamp"])
                writer.writerow([report_id, reason, _now_iso()])
    except OSError as e:
        log.warning("layer2_failure_log_failed", error=str(e))


def validate_and_sanitize_layer2(
    tool_input: Any,
    report_id: Any = None,
) -> tuple[dict | None, list[dict]]:
    """Layer 2 LLM 결과를 검증하고 가능한 오류를 보정한다.

    Args:
        tool_input: LLM에서 받은 raw dict (또는 JSON 문자열).
        report_id: 로깅용 report ID (없어도 됨).

    Returns:
        (sanitized_dict, corrections)
        - sanitized_dict: 보정된 dict. 구조적 오류면 None.
        - corrections: 보정/reject 내역 list[dict]
          각 항목: {"field": str, "original": Any, "corrected": Any, "reason": str}
          reject 시: {"field": "ROOT", "original": ..., "corrected": None, "reason": str}
    """
    corrections: list[dict] = []

    def _correction(field: str, original: Any, corrected: Any, reason: str = "") -> None:
        corrections.append({
            "field": field,
            "original": original,
            "corrected": corrected,
            "reason": reason,
        })
        _append_sanitized(report_id, field, original, corrected)

    def _reject(reason: str, original: Any = None) -> tuple[None, list[dict]]:
        corrections.append({
            "field": "ROOT",
            "original": original,
            "corrected": None,
            "reason": reason,
        })
        _append_failure(report_id, reason)
        log.warning("layer2_validation_rejected", report_id=report_id, reason=reason)
        return None, corrections

    # ── 1. 타입 체크 ──────────────────────────────────────
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except (json.JSONDecodeError, ValueError) as e:
            return _reject(f"json_parse_failed: {e}", tool_input)

    if not isinstance(tool_input, dict):
        return _reject(f"not_a_dict: got {type(tool_input).__name__}", tool_input)

    # Work on a shallow copy to avoid mutating caller's data
    data: dict = dict(tool_input)

    # ── 2. report_category ─────────────────────────────────
    raw_cat = data.get("report_category")
    if raw_cat is None:
        return _reject("missing_report_category")

    if not isinstance(raw_cat, str):
        return _reject(f"report_category_not_string: {raw_cat!r}")

    lowered = raw_cat.lower().strip()
    if lowered in _VALID_CATEGORIES:
        if lowered != raw_cat:
            _correction("report_category", raw_cat, lowered, "lowercased")
        data["report_category"] = lowered
    elif lowered in _CATEGORY_MAP:
        mapped = _CATEGORY_MAP[lowered]
        _correction("report_category", raw_cat, mapped, "korean_mapped")
        data["report_category"] = mapped
    else:
        return _reject(f"invalid_report_category: {raw_cat!r}")

    # ── 3. category_confidence ─────────────────────────────
    raw_conf = data.get("category_confidence", 1.0)
    try:
        conf = float(raw_conf)
    except (ValueError, TypeError):
        _correction("category_confidence", raw_conf, 1.0, "float_cast_failed_defaulted")
        conf = 1.0
    clamped = min(1.0, max(0.0, conf))
    if clamped != conf:
        _correction("category_confidence", raw_conf, clamped, "clamped_0_1")
    data["category_confidence"] = clamped

    # ── 4. chain ───────────────────────────────────────────
    raw_chain = data.get("chain")
    if raw_chain is None:
        # chain is required per schema — reject
        return _reject("missing_chain")

    if isinstance(raw_chain, dict):
        # Single dict → wrap in list
        _correction("chain", raw_chain, [raw_chain], "dict_wrapped_in_list")
        data["chain"] = [raw_chain]
    elif not isinstance(raw_chain, list):
        return _reject(f"chain_not_list_or_dict: {type(raw_chain).__name__}")
    # chain is now a list; individual items are not validated here (LLM responsibility)

    # ── 5. stock_mentions ──────────────────────────────────
    raw_sm = data.get("stock_mentions", [])
    if not isinstance(raw_sm, list):
        return _reject(f"stock_mentions_not_list: {type(raw_sm).__name__}")

    filtered_sm = []
    for item in raw_sm:
        if not isinstance(item, dict):
            _correction("stock_mentions[item]", item, None, "dropped_non_dict")
            continue
        if not item.get("company_name") and not item.get("stock_code"):
            _correction("stock_mentions[item]", item, None, "dropped_missing_company_name")
            continue
        if not item.get("mention_type"):
            _correction("stock_mentions[item]", item, None, "dropped_missing_mention_type")
            continue
        filtered_sm.append(item)

    if len(filtered_sm) != len(raw_sm):
        _correction(
            "stock_mentions",
            f"len={len(raw_sm)}",
            f"len={len(filtered_sm)}",
            "items_dropped_missing_required_fields",
        )
    data["stock_mentions"] = filtered_sm

    # ── 6. sector_mentions ─────────────────────────────────
    raw_sect = data.get("sector_mentions", [])
    if not isinstance(raw_sect, list):
        return _reject(f"sector_mentions_not_list: {type(raw_sect).__name__}")

    filtered_sect = []
    for item in raw_sect:
        if not isinstance(item, dict):
            _correction("sector_mentions[item]", item, None, "dropped_non_dict")
            continue
        if not item.get("sector"):
            _correction("sector_mentions[item]", item, None, "dropped_missing_sector")
            continue
        if not item.get("mention_type"):
            _correction("sector_mentions[item]", item, None, "dropped_missing_mention_type")
            continue
        filtered_sect.append(item)

    if len(filtered_sect) != len(raw_sect):
        _correction(
            "sector_mentions",
            f"len={len(raw_sect)}",
            f"len={len(filtered_sect)}",
            "items_dropped_missing_required_fields",
        )
    data["sector_mentions"] = filtered_sect

    # ── 7. keywords ────────────────────────────────────────
    raw_kw = data.get("keywords", [])
    if not isinstance(raw_kw, list):
        return _reject(f"keywords_not_list: {type(raw_kw).__name__}")

    filtered_kw = []
    for item in raw_kw:
        if not isinstance(item, dict):
            _correction("keywords[item]", item, None, "dropped_non_dict")
            continue
        if not item.get("keyword"):
            _correction("keywords[item]", item, None, "dropped_missing_keyword")
            continue
        filtered_kw.append(item)

    if len(filtered_kw) != len(raw_kw):
        _correction(
            "keywords",
            f"len={len(raw_kw)}",
            f"len={len(filtered_kw)}",
            "items_dropped_missing_required_fields",
        )
    data["keywords"] = filtered_kw

    # ── 8. thesis ──────────────────────────────────────────
    raw_thesis = data.get("thesis")
    if raw_thesis is None or not isinstance(raw_thesis, dict):
        default_thesis = {"summary": "", "sentiment": 0.0}
        _correction("thesis", raw_thesis, default_thesis, "defaulted_missing_or_non_dict")
        data["thesis"] = default_thesis

    # ── 9. meta ────────────────────────────────────────────
    raw_meta = data.get("meta")
    if raw_meta is None or not isinstance(raw_meta, dict):
        _correction("meta", raw_meta, {}, "defaulted_non_dict_to_empty")
        data["meta"] = {}

    # ── 10. extraction_quality ─────────────────────────────
    raw_eq = data.get("extraction_quality", "medium")
    if not isinstance(raw_eq, str):
        _correction("extraction_quality", raw_eq, "medium", "defaulted_non_string")
        data["extraction_quality"] = "medium"
    else:
        lowered_eq = raw_eq.lower().strip()
        if lowered_eq in _VALID_QUALITY:
            if lowered_eq != raw_eq:
                _correction("extraction_quality", raw_eq, lowered_eq, "lowercased")
            data["extraction_quality"] = lowered_eq
        else:
            _correction("extraction_quality", raw_eq, "medium", "invalid_enum_defaulted")
            data["extraction_quality"] = "medium"

    return data, corrections
