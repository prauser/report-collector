"""Tests for scripts/codex_layer2.py.

Mock subprocess.run so no actual Codex CLI or subscription call is needed.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.codex_layer2 import (
    _build_prompt,
    _is_rate_limited,
    _load_done_ids,
    _parse_json_from_result,
    _parse_codex_tokens_used,
    _process_one,
    run,
)


def _make_valid_layer2() -> dict:
    return {
        "report_category": "stock",
        "category_confidence": 0.9,
        "meta": {"broker": "삼성증권", "analyst": "홍길동"},
        "thesis": {"summary": "실적 호조 예상", "sentiment": 0.8},
        "chain": [
            {"step": "trigger", "text": "반도체 업황 회복", "direction": "positive", "confidence": "high"}
        ],
        "extraction_quality": "high",
        "stock_mentions": [
            {"company_name": "삼성전자", "mention_type": "primary", "stock_code": "005930"}
        ],
        "sector_mentions": [],
        "keywords": [{"keyword": "반도체", "keyword_type": "industry"}],
    }


def _make_subprocess_result(stdout: str = "", stderr: str = "", returncode: int = 0) -> MagicMock:
    mock = MagicMock(spec=subprocess.CompletedProcess)
    mock.stdout = stdout
    mock.stderr = stderr
    mock.returncode = returncode
    return mock


def _write_output_from_cmd(cmd: list[str], payload: dict) -> None:
    output_path = Path(cmd[cmd.index("-o") + 1])
    output_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class TestParseJsonFromResult:
    def test_direct_json(self):
        data = {"report_category": "stock"}
        assert _parse_json_from_result(json.dumps(data)) == data

    def test_json_code_block(self):
        assert _parse_json_from_result('```json\n{"report_category":"macro"}\n```') == {
            "report_category": "macro"
        }

    def test_invalid_returns_none(self):
        assert _parse_json_from_result("not json") is None


class TestRateLimit:
    def test_detects_429(self):
        assert _is_rate_limited(1, "Error 429 Too Many Requests")

    def test_success_is_not_rate_limited(self):
        assert not _is_rate_limited(0, "429")


class TestTokenUsage:
    def test_parse_tokens_used(self):
        stdout = "tokens used\n2,607\n"
        assert _parse_codex_tokens_used(stdout) == 2607

    def test_parse_tokens_used_missing(self):
        assert _parse_codex_tokens_used("no token summary") is None


class TestProcessOne:
    def _item(self, report_id=1, user_content="리포트 내용"):
        return {"report_id": report_id, "user_content": user_content}

    def test_success_reads_output_file(self):
        valid = _make_valid_layer2()

        def _mock_run(cmd, **kwargs):
            _write_output_from_cmd(cmd, valid)
            return _make_subprocess_result(stdout="tokens used\n12,345\n")

        with patch("subprocess.run", side_effect=_mock_run):
            result = _process_one(self._item(), timeout=30)

        assert result["status"] == "success"
        assert result["result"]["report_category"] == "stock"
        assert isinstance(result["sanitize_log"], list)
        assert result["codex_tokens_used"] == 12345
        assert result["codex_model"] is None
        assert result["codex_effort"] is None

    def test_success_falls_back_to_stdout(self):
        valid = _make_valid_layer2()

        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=json.dumps(valid))):
            result = _process_one(self._item(), timeout=30)

        assert result["status"] == "success"

    def test_subprocess_error(self):
        with patch("subprocess.run", return_value=_make_subprocess_result(returncode=1, stderr="boom")):
            result = _process_one(self._item(), timeout=30)

        assert result["status"] == "failed"
        assert result["error"] == "subprocess_error:1"
        assert "stderr" in result

    def test_validation_failed(self):
        invalid = {"meta": {}, "thesis": {}, "chain": []}

        def _mock_run(cmd, **kwargs):
            _write_output_from_cmd(cmd, invalid)
            return _make_subprocess_result()

        with patch("subprocess.run", side_effect=_mock_run):
            result = _process_one(self._item(), timeout=30)

        assert result["status"] == "failed"
        assert result["error"] == "validation_failed"

    def test_prompt_passed_via_stdin_not_command_arg(self):
        valid = _make_valid_layer2()
        captured = {}
        item = self._item(user_content="X" * 1000)
        prompt = _build_prompt(item["user_content"])

        def _mock_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            _write_output_from_cmd(cmd, valid)
            return _make_subprocess_result()

        with patch("subprocess.run", side_effect=_mock_run):
            result = _process_one(item, timeout=30)

        assert result["status"] == "success"
        assert captured["kwargs"]["input"] == prompt
        assert prompt not in captured["cmd"]
        assert "X" * 1000 not in " ".join(str(part) for part in captured["cmd"])

    def test_codex_command_uses_exec_output_file_and_read_only(self):
        valid = _make_valid_layer2()
        captured_cmd = []

        def _mock_run(cmd, **kwargs):
            captured_cmd.extend(cmd)
            _write_output_from_cmd(cmd, valid)
            return _make_subprocess_result()

        with patch("subprocess.run", side_effect=_mock_run):
            _process_one(self._item(), timeout=30, model="gpt-5.4", effort="medium")

        assert captured_cmd[1:3] == ["exec", "-"]
        assert "--ephemeral" in captured_cmd
        assert "--sandbox" in captured_cmd
        assert "read-only" in captured_cmd
        assert "-o" in captured_cmd
        assert "--model" in captured_cmd
        assert "gpt-5.4" in captured_cmd
        assert "-c" in captured_cmd
        assert "model_reasoning_effort=medium" in captured_cmd


class TestLoadDoneIds:
    def test_loads_only_success_ids(self, tmp_path):
        p = tmp_path / "out.jsonl"
        p.write_text(
            "\n".join(
                [
                    json.dumps({"report_id": 1, "status": "success"}),
                    json.dumps({"report_id": 2, "status": "failed"}),
                ]
            ),
            encoding="utf-8",
        )

        assert _load_done_ids(p) == {1}


class TestRun:
    def test_run_writes_jsonl(self, tmp_path):
        input_path = tmp_path / "inputs.jsonl"
        output_path = tmp_path / "outputs.jsonl"
        items = [{"report_id": i, "user_content": f"content {i}"} for i in range(1, 4)]
        input_path.write_text("\n".join(json.dumps(i) for i in items), encoding="utf-8")
        valid = _make_valid_layer2()

        def _mock_process(item, timeout, codex_cmd="codex", cwd=None, model=None, effort=None):
            return {
                "report_id": item["report_id"],
                "result": valid,
                "sanitize_log": [],
                "codex_tokens_used": None,
                "codex_model": model,
                "codex_effort": effort,
                "status": "success",
                "elapsed_sec": 0.1,
            }

        args = SimpleNamespace(
            input=str(input_path),
            output=str(output_path),
            concurrency=2,
            max_daily=0,
            timeout=30,
            codex_cmd="codex",
            cwd=None,
            model=None,
            effort=None,
        )

        with patch("scripts.codex_layer2._process_one", side_effect=_mock_process):
            run(args)

        records = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
        assert len(records) == 3
        assert {r["report_id"] for r in records} == {1, 2, 3}

    def test_max_daily_limits_processing(self, tmp_path):
        input_path = tmp_path / "inputs.jsonl"
        output_path = tmp_path / "outputs.jsonl"
        items = [{"report_id": i, "user_content": f"content {i}"} for i in range(1, 6)]
        input_path.write_text("\n".join(json.dumps(i) for i in items), encoding="utf-8")

        args = SimpleNamespace(
            input=str(input_path),
            output=str(output_path),
            concurrency=1,
            max_daily=2,
            timeout=30,
            codex_cmd="codex",
            cwd=None,
            model=None,
            effort=None,
        )

        with patch("scripts.codex_layer2._process_one") as mock_process:
            mock_process.side_effect = lambda item, **kwargs: {
                "report_id": item["report_id"],
                "result": _make_valid_layer2(),
                "sanitize_log": [],
                "codex_tokens_used": None,
                "status": "success",
                "elapsed_sec": 0.1,
            }
            run(args)

        assert mock_process.call_count == 2
