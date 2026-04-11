"""Tests for parser/layer2_validator.py — validate_and_sanitize_layer2()."""
from __future__ import annotations

import json
import csv
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from parser.layer2_validator import validate_and_sanitize_layer2


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _valid_input(**overrides) -> dict:
    """Return a minimal valid tool_input dict."""
    base = {
        "report_category": "stock",
        "category_confidence": 0.9,
        "meta": {"broker": "삼성증권"},
        "thesis": {"summary": "핵심 논지", "sentiment": 0.5},
        "chain": [{"step": "trigger", "text": "HBM 수요 증가"}],
        "extraction_quality": "high",
        "stock_mentions": [],
        "sector_mentions": [],
        "keywords": [],
    }
    base.update(overrides)
    return base


# ──────────────────────────────────────────────────────────────
# 1. 정상 입력 통과
# ──────────────────────────────────────────────────────────────

class TestValidInput:

    def test_valid_stock_passes(self):
        result, corrections = validate_and_sanitize_layer2(_valid_input())
        assert result is not None
        assert result["report_category"] == "stock"

    def test_valid_industry_passes(self):
        data = _valid_input(report_category="industry")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["report_category"] == "industry"

    def test_valid_macro_passes(self):
        data = _valid_input(report_category="macro")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["report_category"] == "macro"

    def test_no_corrections_on_clean_input(self):
        result, corrections = validate_and_sanitize_layer2(_valid_input())
        assert result is not None
        # No corrections expected for perfectly clean input
        assert corrections == []

    def test_returns_copy_not_same_object(self):
        original = _valid_input()
        result, _ = validate_and_sanitize_layer2(original)
        assert result is not original


# ──────────────────────────────────────────────────────────────
# 2. report_category 보정
# ──────────────────────────────────────────────────────────────

class TestReportCategory:

    def test_uppercase_lowercased(self):
        data = _valid_input(report_category="Stock")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["report_category"] == "stock"
        assert any(c["field"] == "report_category" for c in corrections)

    def test_all_caps_lowercased(self):
        data = _valid_input(report_category="MACRO")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["report_category"] == "macro"

    def test_korean_macro_mapped(self):
        data = _valid_input(report_category="경제")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["report_category"] == "macro"
        assert any(c["field"] == "report_category" and c["reason"] == "korean_mapped" for c in corrections)

    def test_korean_stock_mapped(self):
        data = _valid_input(report_category="종목")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["report_category"] == "stock"

    def test_korean_industry_mapped(self):
        data = _valid_input(report_category="산업")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["report_category"] == "industry"

    def test_korean_macro_alt_mapped(self):
        data = _valid_input(report_category="거시경제")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["report_category"] == "macro"

    def test_invalid_category_rejected(self):
        data = _valid_input(report_category="unknown_type")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is None
        assert any(c["reason"].startswith("invalid_report_category") for c in corrections)

    def test_missing_category_rejected(self):
        data = _valid_input()
        del data["report_category"]
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is None
        assert any(c["reason"] == "missing_report_category" for c in corrections)

    def test_non_string_category_rejected(self):
        data = _valid_input(report_category=123)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is None


# ──────────────────────────────────────────────────────────────
# 3. category_confidence clamp
# ──────────────────────────────────────────────────────────────

class TestCategoryConfidence:

    def test_above_1_clamped_to_1(self):
        data = _valid_input(category_confidence=1.5)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["category_confidence"] == 1.0
        assert any(c["field"] == "category_confidence" and "clamp" in c["reason"] for c in corrections)

    def test_below_0_clamped_to_0(self):
        data = _valid_input(category_confidence=-0.5)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["category_confidence"] == 0.0

    def test_valid_confidence_unchanged(self):
        data = _valid_input(category_confidence=0.75)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["category_confidence"] == 0.75
        # No correction for confidence
        assert not any(c["field"] == "category_confidence" for c in corrections)

    def test_string_float_cast(self):
        data = _valid_input(category_confidence="0.8")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["category_confidence"] == 0.8

    def test_non_numeric_defaults_to_1(self):
        data = _valid_input(category_confidence="invalid")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["category_confidence"] == 1.0
        assert any(c["field"] == "category_confidence" and "failed" in c["reason"] for c in corrections)

    def test_missing_confidence_defaults_to_1(self):
        data = _valid_input()
        del data["category_confidence"]
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["category_confidence"] == 1.0


# ──────────────────────────────────────────────────────────────
# 4. chain 보정
# ──────────────────────────────────────────────────────────────

class TestChain:

    def test_chain_dict_wrapped_in_list(self):
        single_step = {"step": "trigger", "text": "test"}
        data = _valid_input(chain=single_step)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert isinstance(result["chain"], list)
        assert len(result["chain"]) == 1
        assert result["chain"][0] == single_step
        assert any(c["field"] == "chain" and "dict_wrapped" in c["reason"] for c in corrections)

    def test_chain_list_passes_through(self):
        steps = [{"step": "trigger", "text": "a"}, {"step": "mechanism", "text": "b"}]
        data = _valid_input(chain=steps)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert len(result["chain"]) == 2
        assert not any(c["field"] == "chain" for c in corrections)

    def test_chain_string_rejected(self):
        data = _valid_input(chain="not a list")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is None
        assert any("chain_not_list_or_dict" in c["reason"] for c in corrections)

    def test_chain_missing_rejected(self):
        data = _valid_input()
        del data["chain"]
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is None
        assert any(c["reason"] == "missing_chain" for c in corrections)

    def test_empty_chain_list_allowed(self):
        data = _valid_input(chain=[])
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["chain"] == []


# ──────────────────────────────────────────────────────────────
# 5. stock_mentions 항목 드롭
# ──────────────────────────────────────────────────────────────

class TestStockMentions:

    def test_valid_items_kept(self):
        items = [
            {"company_name": "삼성전자", "mention_type": "primary"},
            {"company_name": "SK하이닉스", "mention_type": "related"},
        ]
        data = _valid_input(stock_mentions=items)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert len(result["stock_mentions"]) == 2

    def test_item_missing_company_name_dropped(self):
        items = [
            {"stock_code": "", "mention_type": "primary"},   # no company_name, no code
            {"company_name": "삼성전자", "mention_type": "primary"},
        ]
        data = _valid_input(stock_mentions=items)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert len(result["stock_mentions"]) == 1
        assert result["stock_mentions"][0]["company_name"] == "삼성전자"

    def test_item_missing_mention_type_dropped(self):
        items = [
            {"company_name": "삼성전자"},   # missing mention_type
            {"company_name": "SK하이닉스", "mention_type": "related"},
        ]
        data = _valid_input(stock_mentions=items)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert len(result["stock_mentions"]) == 1

    def test_item_with_stock_code_no_name_kept(self):
        """stock_code만 있어도 통과 (company_name은 없지만 stock_code가 있음)."""
        items = [{"stock_code": "005930", "mention_type": "primary"}]
        data = _valid_input(stock_mentions=items)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert len(result["stock_mentions"]) == 1

    def test_non_list_rejected(self):
        data = _valid_input(stock_mentions={"company_name": "삼성전자"})
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is None
        assert any("stock_mentions_not_list" in c["reason"] for c in corrections)

    def test_non_dict_items_dropped(self):
        items = ["string_item", {"company_name": "삼성전자", "mention_type": "primary"}]
        data = _valid_input(stock_mentions=items)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert len(result["stock_mentions"]) == 1


# ──────────────────────────────────────────────────────────────
# 6. sector_mentions 항목 드롭
# ──────────────────────────────────────────────────────────────

class TestSectorMentions:

    def test_valid_items_kept(self):
        items = [{"sector": "반도체", "mention_type": "primary"}]
        data = _valid_input(sector_mentions=items)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert len(result["sector_mentions"]) == 1

    def test_missing_sector_dropped(self):
        items = [
            {"mention_type": "primary"},            # no sector
            {"sector": "반도체", "mention_type": "primary"},
        ]
        data = _valid_input(sector_mentions=items)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert len(result["sector_mentions"]) == 1

    def test_non_list_rejected(self):
        data = _valid_input(sector_mentions="반도체")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is None


# ──────────────────────────────────────────────────────────────
# 7. keywords 항목 드롭
# ──────────────────────────────────────────────────────────────

class TestKeywords:

    def test_valid_items_kept(self):
        items = [{"keyword": "HBM", "keyword_type": "product"}]
        data = _valid_input(keywords=items)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert len(result["keywords"]) == 1

    def test_missing_keyword_dropped(self):
        items = [
            {"keyword_type": "product"},    # missing keyword
            {"keyword": "HBM"},
        ]
        data = _valid_input(keywords=items)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert len(result["keywords"]) == 1

    def test_non_list_rejected(self):
        data = _valid_input(keywords="HBM")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is None


# ──────────────────────────────────────────────────────────────
# 8. thesis 기본값
# ──────────────────────────────────────────────────────────────

class TestThesis:

    def test_missing_thesis_gets_default(self):
        data = _valid_input()
        del data["thesis"]
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["thesis"] == {"summary": "", "sentiment": 0.0}
        assert any(c["field"] == "thesis" for c in corrections)

    def test_none_thesis_gets_default(self):
        data = _valid_input(thesis=None)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["thesis"] == {"summary": "", "sentiment": 0.0}

    def test_string_thesis_gets_default(self):
        data = _valid_input(thesis="some text")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["thesis"] == {"summary": "", "sentiment": 0.0}

    def test_valid_thesis_unchanged(self):
        thesis = {"summary": "좋은 투자 논리", "sentiment": 0.8}
        data = _valid_input(thesis=thesis)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["thesis"] == thesis
        assert not any(c["field"] == "thesis" for c in corrections)


# ──────────────────────────────────────────────────────────────
# 9. meta 기본값
# ──────────────────────────────────────────────────────────────

class TestMeta:

    def test_none_meta_becomes_empty_dict(self):
        data = _valid_input(meta=None)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["meta"] == {}
        assert any(c["field"] == "meta" for c in corrections)

    def test_string_meta_becomes_empty_dict(self):
        data = _valid_input(meta="not a dict")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["meta"] == {}

    def test_valid_meta_unchanged(self):
        meta = {"broker": "삼성증권", "analyst": "홍길동"}
        data = _valid_input(meta=meta)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["meta"] == meta
        assert not any(c["field"] == "meta" for c in corrections)


# ──────────────────────────────────────────────────────────────
# 10. extraction_quality 보정
# ──────────────────────────────────────────────────────────────

class TestExtractionQuality:

    def test_uppercase_lowercased(self):
        data = _valid_input(extraction_quality="High")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["extraction_quality"] == "high"

    def test_invalid_defaults_to_medium(self):
        data = _valid_input(extraction_quality="excellent")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["extraction_quality"] == "medium"
        assert any(c["field"] == "extraction_quality" and "invalid_enum" in c["reason"] for c in corrections)

    def test_valid_high_unchanged(self):
        data = _valid_input(extraction_quality="high")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["extraction_quality"] == "high"
        assert not any(c["field"] == "extraction_quality" for c in corrections)

    def test_truncated_valid(self):
        data = _valid_input(extraction_quality="truncated")
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["extraction_quality"] == "truncated"

    def test_missing_defaults_to_medium(self):
        data = _valid_input()
        del data["extraction_quality"]
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["extraction_quality"] == "medium"

    def test_non_string_defaults_to_medium(self):
        data = _valid_input(extraction_quality=42)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["extraction_quality"] == "medium"


# ──────────────────────────────────────────────────────────────
# 11. 구조적 reject
# ──────────────────────────────────────────────────────────────

class TestReject:

    def test_none_input_rejected(self):
        result, corrections = validate_and_sanitize_layer2(None)
        assert result is None
        assert any("not_a_dict" in c["reason"] for c in corrections)

    def test_list_input_rejected(self):
        result, corrections = validate_and_sanitize_layer2([1, 2, 3])
        assert result is None

    def test_integer_input_rejected(self):
        result, corrections = validate_and_sanitize_layer2(42)
        assert result is None

    def test_json_string_parsed_and_validated(self):
        valid = _valid_input()
        json_str = json.dumps(valid)
        result, corrections = validate_and_sanitize_layer2(json_str)
        assert result is not None
        assert result["report_category"] == "stock"

    def test_invalid_json_string_rejected(self):
        result, corrections = validate_and_sanitize_layer2("{not valid json}")
        assert result is None
        assert any("json_parse_failed" in c["reason"] for c in corrections)

    def test_completely_empty_dict_rejected(self):
        result, corrections = validate_and_sanitize_layer2({})
        assert result is None


# ──────────────────────────────────────────────────────────────
# 12. corrections list structure
# ──────────────────────────────────────────────────────────────

class TestCorrectionsStructure:

    def test_correction_has_required_keys(self):
        data = _valid_input(report_category="Stock", category_confidence=1.5)
        result, corrections = validate_and_sanitize_layer2(data)
        for c in corrections:
            assert "field" in c
            assert "original" in c
            assert "corrected" in c
            assert "reason" in c

    def test_reject_correction_field_is_ROOT(self):
        result, corrections = validate_and_sanitize_layer2(None)
        assert result is None
        root_corrections = [c for c in corrections if c["field"] == "ROOT"]
        assert len(root_corrections) >= 1

    def test_multiple_corrections_accumulated(self):
        data = _valid_input(
            report_category="Stock",        # will be lowercased
            category_confidence=1.5,        # will be clamped
            thesis=None,                    # will be defaulted
        )
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        fields = [c["field"] for c in corrections]
        assert "report_category" in fields
        assert "category_confidence" in fields
        assert "thesis" in fields


# ──────────────────────────────────────────────────────────────
# 13. CSV logging
# ──────────────────────────────────────────────────────────────

class TestCsvLogging:

    def test_sanitized_csv_written_on_correction(self, tmp_path):
        sanitized_path = tmp_path / "layer2_sanitized.csv"
        failures_path = tmp_path / "layer2_validation_failures.csv"

        with patch("parser.layer2_validator._SANITIZED_CSV", sanitized_path), \
             patch("parser.layer2_validator._FAILURES_CSV", failures_path), \
             patch("parser.layer2_validator._LOGS_DIR", tmp_path):
            data = _valid_input(report_category="Stock")
            result, corrections = validate_and_sanitize_layer2(data, report_id=42)

        assert sanitized_path.exists()
        rows = list(csv.DictReader(sanitized_path.open(encoding="utf-8")))
        assert len(rows) >= 1
        assert any(r["field"] == "report_category" for r in rows)
        assert all(r["report_id"] == "42" for r in rows)

    def test_failure_csv_written_on_reject(self, tmp_path):
        sanitized_path = tmp_path / "layer2_sanitized.csv"
        failures_path = tmp_path / "layer2_validation_failures.csv"

        with patch("parser.layer2_validator._SANITIZED_CSV", failures_path), \
             patch("parser.layer2_validator._FAILURES_CSV", failures_path), \
             patch("parser.layer2_validator._LOGS_DIR", tmp_path):
            result, corrections = validate_and_sanitize_layer2(None, report_id=99)

        assert failures_path.exists()
        rows = list(csv.DictReader(failures_path.open(encoding="utf-8")))
        assert len(rows) >= 1

    def test_no_csv_written_for_clean_input(self, tmp_path):
        sanitized_path = tmp_path / "layer2_sanitized.csv"
        failures_path = tmp_path / "layer2_validation_failures.csv"

        with patch("parser.layer2_validator._SANITIZED_CSV", sanitized_path), \
             patch("parser.layer2_validator._FAILURES_CSV", failures_path), \
             patch("parser.layer2_validator._LOGS_DIR", tmp_path):
            result, corrections = validate_and_sanitize_layer2(_valid_input())

        assert not sanitized_path.exists()
        assert not failures_path.exists()


# ──────────────────────────────────────────────────────────────
# 14. make_layer2_result integration
# ──────────────────────────────────────────────────────────────

class TestMakeLayer2ResultIntegration:
    """Verify that make_layer2_result() calls validate_and_sanitize_layer2()."""

    def test_invalid_category_returns_none(self):
        from parser.layer2_extractor import make_layer2_result

        tool_input = _valid_input(report_category="INVALID")
        with patch("parser.layer2_extractor.settings") as s:
            s.llm_pdf_model = "claude-sonnet-4-6"
            s.analysis_schema_version = "v1"
            result = make_layer2_result(tool_input, 100, 50)
        assert result is None

    def test_valid_input_returns_layer2result(self):
        from parser.layer2_extractor import make_layer2_result, Layer2Result

        tool_input = _valid_input()
        with patch("parser.layer2_extractor.settings") as s:
            s.llm_pdf_model = "claude-sonnet-4-6"
            s.analysis_schema_version = "v1"
            result = make_layer2_result(tool_input, 100, 50)
        assert isinstance(result, Layer2Result)
        assert result.report_category == "stock"

    def test_uppercase_category_corrected_and_passes(self):
        from parser.layer2_extractor import make_layer2_result, Layer2Result

        tool_input = _valid_input(report_category="Industry")
        with patch("parser.layer2_extractor.settings") as s:
            s.llm_pdf_model = "claude-sonnet-4-6"
            s.analysis_schema_version = "v1"
            result = make_layer2_result(tool_input, 100, 50)
        assert isinstance(result, Layer2Result)
        assert result.report_category == "industry"

    def test_none_tool_input_returns_none(self):
        from parser.layer2_extractor import make_layer2_result

        result = make_layer2_result(None, 100, 50)
        assert result is None


# ──────────────────────────────────────────────────────────────
# 15. Fix 3: integer confidence — no spurious correction log
# ──────────────────────────────────────────────────────────────

class TestIntegerConfidenceNoSpuriousCorrection:
    """Regression: category_confidence=1 (int) must not trigger a clamped_0_1 correction."""

    def test_integer_one_no_correction(self):
        """confidence=1 (int) is valid; 1.0 == 1.0 after float cast so no clamped_0_1 logged."""
        data = _valid_input(category_confidence=1)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["category_confidence"] == 1.0
        # The old bug: clamped(1.0) != raw_conf(1) was True (float != int), triggering correction.
        # With the fix: clamped(1.0) != conf(1.0) is False, so no correction.
        clamp_corrections = [c for c in corrections if c["field"] == "category_confidence" and "clamp" in c["reason"]]
        assert clamp_corrections == [], (
            f"Spurious clamped_0_1 correction logged for integer confidence=1: {clamp_corrections}"
        )

    def test_integer_zero_no_correction(self):
        """confidence=0 (int) — no clamped_0_1 correction."""
        data = _valid_input(category_confidence=0)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["category_confidence"] == 0.0
        clamp_corrections = [c for c in corrections if c["field"] == "category_confidence" and "clamp" in c["reason"]]
        assert clamp_corrections == []

    def test_float_1_no_correction(self):
        """confidence=1.0 (float) — no correction either."""
        data = _valid_input(category_confidence=1.0)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        clamp_corrections = [c for c in corrections if c["field"] == "category_confidence" and "clamp" in c["reason"]]
        assert clamp_corrections == []

    def test_out_of_range_still_triggers_correction(self):
        """Sanity: out-of-range values still do trigger the correction."""
        data = _valid_input(category_confidence=2)
        result, corrections = validate_and_sanitize_layer2(data)
        assert result is not None
        assert result["category_confidence"] == 1.0
        clamp_corrections = [c for c in corrections if "clamp" in c["reason"]]
        assert len(clamp_corrections) == 1


# ──────────────────────────────────────────────────────────────
# 16. Fix 2: threading.Lock guards CSV helpers (TOCTOU)
# ──────────────────────────────────────────────────────────────

class TestCsvHelperThreadSafety:
    """Regression: _append_sanitized and _append_failure must use the module-level
    _CSV_LOCK so concurrent threads don't race on the header-check + open."""

    def test_csv_lock_exists_on_module(self):
        import parser.layer2_validator as mod
        assert hasattr(mod, "_CSV_LOCK"), "_CSV_LOCK not found on module"
        assert isinstance(mod._CSV_LOCK, type(threading.Lock())), "_CSV_LOCK is not a Lock"

    def test_concurrent_corrections_all_written(self, tmp_path):
        """Many threads writing corrections — header appears exactly once, all rows present."""
        sanitized_path = tmp_path / "layer2_sanitized.csv"
        failures_path = tmp_path / "layer2_validation_failures.csv"

        n_threads = 20
        errors = []

        def _write_one(i):
            try:
                data = _valid_input(report_category="Stock")  # triggers a correction
                validate_and_sanitize_layer2(data, report_id=i)
            except Exception as e:
                errors.append(e)

        # Patch at test level, outside threads, so all threads share the same paths
        with patch("parser.layer2_validator._SANITIZED_CSV", sanitized_path), \
             patch("parser.layer2_validator._FAILURES_CSV", failures_path), \
             patch("parser.layer2_validator._LOGS_DIR", tmp_path):

            threads = [threading.Thread(target=_write_one, args=(i,)) for i in range(n_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert errors == [], f"Errors during concurrent CSV writes: {errors}"
        assert sanitized_path.exists()

        rows = list(csv.DictReader(sanitized_path.open(encoding="utf-8")))
        # Each thread writes one correction (report_category lowercased)
        assert len(rows) == n_threads

    def test_concurrent_failures_all_written(self, tmp_path):
        """Many threads writing rejection records — all rows present."""
        sanitized_path = tmp_path / "layer2_sanitized.csv"
        failures_path = tmp_path / "layer2_validation_failures.csv"

        n_threads = 20
        errors = []

        def _write_one(i):
            try:
                validate_and_sanitize_layer2(None, report_id=i)  # always rejects
            except Exception as e:
                errors.append(e)

        # Patch at test level, outside threads, so all threads share the same paths
        with patch("parser.layer2_validator._SANITIZED_CSV", sanitized_path), \
             patch("parser.layer2_validator._FAILURES_CSV", failures_path), \
             patch("parser.layer2_validator._LOGS_DIR", tmp_path):

            threads = [threading.Thread(target=_write_one, args=(i,)) for i in range(n_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

        assert errors == [], f"Errors during concurrent CSV writes: {errors}"
        assert failures_path.exists()
        rows = list(csv.DictReader(failures_path.open(encoding="utf-8")))
        assert len(rows) == n_threads
