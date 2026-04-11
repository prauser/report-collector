"""Tests for scripts/claude_layer2.py.

Mock subprocess.run so no actual claude CLI is needed.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

# Ensure repo root is importable
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from scripts.claude_layer2 import (
    _build_prompt,
    _is_rate_limited,
    _load_done_ids,
    _load_inputs,
    _parse_json_from_result,
    _process_one,
    _OutputWriter,
    run,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_valid_layer2() -> dict:
    """유효한 Layer2 결과 dict 반환."""
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


def _make_subprocess_result(
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> MagicMock:
    mock = MagicMock(spec=subprocess.CompletedProcess)
    mock.stdout = stdout
    mock.stderr = stderr
    mock.returncode = returncode
    return mock


def _claude_json_wrapper(model_text: str) -> str:
    """claude --output-format json wrapper 시뮬레이션."""
    return json.dumps({"type": "result", "result": model_text, "session_id": "test-session"})


# ── _parse_json_from_result 테스트 ────────────────────────────────────────────

class TestParseJsonFromResult:
    def test_json_code_block(self):
        text = '```json\n{"report_category": "stock"}\n```'
        result = _parse_json_from_result(text)
        assert result == {"report_category": "stock"}

    def test_json_code_block_with_extra_text(self):
        text = "다음은 분석 결과입니다:\n```json\n{\"report_category\": \"macro\"}\n```\n이상입니다."
        result = _parse_json_from_result(text)
        assert result == {"report_category": "macro"}

    def test_direct_json(self):
        data = {"report_category": "industry", "value": 42}
        result = _parse_json_from_result(json.dumps(data))
        assert result == data

    def test_json_embedded_in_text(self):
        text = '분석 결과: {"report_category": "stock", "meta": {}}'
        result = _parse_json_from_result(text)
        assert result == {"report_category": "stock", "meta": {}}

    def test_plain_code_block(self):
        text = '```\n{"report_category": "macro"}\n```'
        result = _parse_json_from_result(text)
        assert result == {"report_category": "macro"}

    def test_invalid_returns_none(self):
        result = _parse_json_from_result("이것은 JSON이 아닙니다.")
        assert result is None

    def test_empty_string_returns_none(self):
        result = _parse_json_from_result("")
        assert result is None

    def test_none_like_empty_text(self):
        result = _parse_json_from_result("   ")
        assert result is None


# ── _is_rate_limited 테스트 ───────────────────────────────────────────────────

class TestIsRateLimited:
    def test_returns_false_on_success(self):
        assert not _is_rate_limited(0, "")

    def test_detects_429_in_stderr(self):
        assert _is_rate_limited(1, "Error 429 Too Many Requests")

    def test_detects_rate_limit_text(self):
        assert _is_rate_limited(1, "rate_limit exceeded")

    def test_detects_rate_limit_space(self):
        assert _is_rate_limited(1, "Rate Limit Error occurred")

    def test_non_rate_limit_error(self):
        assert not _is_rate_limited(1, "connection refused")

    def test_zero_returncode_ignores_stderr(self):
        # returncode=0 이면 rate limit이 아님 (정상 종료)
        assert not _is_rate_limited(0, "429 in log but success")


# ── _load_inputs / _load_done_ids 테스트 ─────────────────────────────────────

class TestFileLoaders:
    def test_load_inputs(self, tmp_path):
        p = tmp_path / "inputs.jsonl"
        items = [
            {"report_id": 1, "user_content": "content1"},
            {"report_id": 2, "user_content": "content2"},
        ]
        p.write_text("\n".join(json.dumps(i) for i in items), encoding="utf-8")
        loaded = _load_inputs(p)
        assert len(loaded) == 2
        assert loaded[0]["report_id"] == 1
        assert loaded[1]["report_id"] == 2

    def test_load_inputs_skips_blank_lines(self, tmp_path):
        p = tmp_path / "inputs.jsonl"
        p.write_text('{"report_id": 10}\n\n{"report_id": 20}\n', encoding="utf-8")
        loaded = _load_inputs(p)
        assert len(loaded) == 2

    def test_load_inputs_missing_file(self, tmp_path):
        loaded = _load_inputs(tmp_path / "nonexistent.jsonl")
        assert loaded == []

    def test_load_done_ids_empty_file(self, tmp_path):
        p = tmp_path / "out.jsonl"
        p.write_text("", encoding="utf-8")
        done = _load_done_ids(p)
        assert done == set()

    def test_load_done_ids_nonexistent(self, tmp_path):
        done = _load_done_ids(tmp_path / "missing.jsonl")
        assert done == set()

    def test_load_done_ids_loads_ids(self, tmp_path):
        p = tmp_path / "out.jsonl"
        records = [
            {"report_id": 100, "status": "success"},
            {"report_id": 200, "status": "failed"},
        ]
        p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
        done = _load_done_ids(p)
        # 성공 건만 skip 대상 — 실패 건은 재시도 가능
        assert done == {100}

    def test_load_done_ids_skips_malformed(self, tmp_path):
        p = tmp_path / "out.jsonl"
        p.write_text('{"report_id": 5, "status": "success"}\nNOT_JSON\n{"report_id": 6, "status": "success"}\n', encoding="utf-8")
        done = _load_done_ids(p)
        assert done == {5, 6}


# ── _OutputWriter 테스트 ──────────────────────────────────────────────────────

class TestOutputWriter:
    def test_appends_jsonl(self, tmp_path):
        p = tmp_path / "out.jsonl"
        writer = _OutputWriter(p)
        writer.append({"report_id": 1, "status": "success"})
        writer.append({"report_id": 2, "status": "failed"})
        lines = p.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["report_id"] == 1
        assert json.loads(lines[1])["report_id"] == 2

    def test_creates_parent_dirs(self, tmp_path):
        p = tmp_path / "subdir" / "out.jsonl"
        writer = _OutputWriter(p)
        writer.append({"report_id": 99})
        assert p.exists()


# ── _process_one 테스트 ───────────────────────────────────────────────────────

class TestProcessOne:
    """subprocess.run을 mock하여 단건 처리 로직 테스트."""

    def _item(self, report_id=1, user_content="리포트 내용"):
        return {"report_id": report_id, "user_content": user_content}

    def test_success(self):
        valid = _make_valid_layer2()
        model_text = f"```json\n{json.dumps(valid, ensure_ascii=False)}\n```"
        stdout = _claude_json_wrapper(model_text)

        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=stdout)):
            result = _process_one(self._item(), timeout=30)

        assert result["status"] == "success"
        assert result["report_id"] == 1
        assert result["result"]["report_category"] == "stock"
        assert "elapsed_sec" in result

    def test_success_sanitize_log_present(self):
        """보정이 없는 경우 sanitize_log가 빈 리스트."""
        valid = _make_valid_layer2()
        stdout = _claude_json_wrapper(f"```json\n{json.dumps(valid)}\n```")

        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=stdout)):
            result = _process_one(self._item(), timeout=30)

        assert "sanitize_log" in result
        assert isinstance(result["sanitize_log"], list)

    def test_json_parse_failed(self):
        stdout = _claude_json_wrapper("이것은 JSON이 아닙니다")

        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=stdout)):
            result = _process_one(self._item(), timeout=30)

        assert result["status"] == "failed"
        assert result["error"] == "json_parse_failed"
        assert result["result"] is None

    def test_subprocess_error(self):
        with patch(
            "subprocess.run",
            return_value=_make_subprocess_result(returncode=1, stderr="some error"),
        ):
            result = _process_one(self._item(), timeout=30)

        assert result["status"] == "failed"
        assert "subprocess_error" in result["error"]

    def test_timeout(self):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=30)):
            result = _process_one(self._item(), timeout=30)

        assert result["status"] == "failed"
        assert result["error"] == "timeout"

    def test_claude_not_found(self):
        with patch("subprocess.run", side_effect=FileNotFoundError("claude not found")):
            result = _process_one(self._item(), timeout=30)

        assert result["status"] == "failed"
        assert result["error"] == "claude_not_found"

    def test_validation_failed(self):
        """스키마 reject 케이스: report_category 없음."""
        invalid = {"meta": {}, "thesis": {}, "chain": []}  # missing report_category
        stdout = _claude_json_wrapper(f"```json\n{json.dumps(invalid)}\n```")

        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=stdout)):
            result = _process_one(self._item(), timeout=30)

        assert result["status"] == "failed"
        assert result["error"] == "validation_failed"
        assert result["result"] is None

    def test_wrapper_json_parsed(self):
        """stdout이 wrapper JSON 형식일 때 result 필드에서 텍스트 추출."""
        valid = _make_valid_layer2()
        model_text = f"```json\n{json.dumps(valid)}\n```"
        wrapper = {"type": "result", "result": model_text, "session_id": "abc"}
        stdout = json.dumps(wrapper)

        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=stdout)):
            result = _process_one(self._item(), timeout=30)

        assert result["status"] == "success"

    def test_direct_json_stdout(self):
        """stdout이 직접 JSON인 경우 (wrapper 없음)."""
        valid = _make_valid_layer2()
        stdout = json.dumps(valid)

        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=stdout)):
            result = _process_one(self._item(), timeout=30)

        assert result["status"] == "success"


# ── Rate limit backoff 테스트 ─────────────────────────────────────────────────

class TestRateLimitBackoff:
    """rate limit 시 exponential backoff 로직 검증."""

    def _item(self):
        return {"report_id": 99, "user_content": "테스트"}

    def test_backoff_then_success(self):
        """처음 2번 rate limit → 3번째 성공."""
        valid = _make_valid_layer2()
        model_text = f"```json\n{json.dumps(valid)}\n```"
        success_stdout = _claude_json_wrapper(model_text)

        rate_limit_result = _make_subprocess_result(returncode=1, stderr="429 Too Many Requests")
        success_result = _make_subprocess_result(stdout=success_stdout)

        call_count = 0
        sleep_calls = []

        def _mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                return rate_limit_result
            return success_result

        with patch("subprocess.run", side_effect=_mock_run), \
             patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            result = _process_one(self._item(), timeout=60)

        assert result["status"] == "success"
        assert call_count == 3
        # 첫 번째 백오프: 30초, 두 번째: 60초
        assert sleep_calls[0] == 30
        assert sleep_calls[1] == 60

    def test_backoff_exhausted(self):
        """3회 모두 rate limit → 실패."""
        rate_limit_result = _make_subprocess_result(returncode=1, stderr="rate_limit exceeded")
        sleep_calls = []

        with patch("subprocess.run", return_value=rate_limit_result), \
             patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            result = _process_one(self._item(), timeout=60)

        assert result["status"] == "failed"
        assert result["error"] == "rate_limit_exhausted"
        # 3번 재시도 → 3번 sleep (각 attempt 후 sleep)
        assert len(sleep_calls) == 3

    def test_backoff_max_cap(self):
        """백오프가 _BACKOFF_MAX(300초)를 초과하지 않음."""
        from scripts.claude_layer2 import _BACKOFF_MAX, _BACKOFF_RETRIES

        rate_limit_result = _make_subprocess_result(returncode=1, stderr="429")
        sleep_calls = []

        with patch("subprocess.run", return_value=rate_limit_result), \
             patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            _process_one(self._item(), timeout=60)

        for s in sleep_calls:
            assert s <= _BACKOFF_MAX


# ── Daily limit 테스트 ────────────────────────────────────────────────────────

class TestDailyLimit:
    def test_max_daily_limits_processing(self, tmp_path):
        """--max-daily 에 도달하면 나머지 항목을 처리하지 않음."""
        input_path = tmp_path / "inputs.jsonl"
        output_path = tmp_path / "outputs.jsonl"

        # 10건 생성
        items = [{"report_id": i, "user_content": f"content {i}"} for i in range(1, 11)]
        input_path.write_text("\n".join(json.dumps(i) for i in items), encoding="utf-8")

        valid = _make_valid_layer2()
        model_text = f"```json\n{json.dumps(valid)}\n```"
        success_stdout = _claude_json_wrapper(model_text)

        args = SimpleNamespace(
            input=str(input_path),
            output=str(output_path),
            concurrency=1,
            max_daily=3,
            timeout=30,
            claude_cmd="claude",
        )

        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=success_stdout)):
            run(args)

        # 3건만 처리되어야 함
        lines = output_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3

    def test_max_daily_zero_means_unlimited(self, tmp_path):
        """--max-daily=0 이면 전체 처리."""
        input_path = tmp_path / "inputs.jsonl"
        output_path = tmp_path / "outputs.jsonl"

        items = [{"report_id": i, "user_content": f"c{i}"} for i in range(1, 6)]
        input_path.write_text("\n".join(json.dumps(i) for i in items), encoding="utf-8")

        valid = _make_valid_layer2()
        model_text = f"```json\n{json.dumps(valid)}\n```"
        success_stdout = _claude_json_wrapper(model_text)

        args = SimpleNamespace(
            input=str(input_path),
            output=str(output_path),
            concurrency=1,
            max_daily=0,
            timeout=30,
            claude_cmd="claude",
        )

        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=success_stdout)):
            run(args)

        lines = output_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 5


# ── Resume (skip already-processed) 테스트 ────────────────────────────────────

class TestResume:
    def test_skip_already_processed(self, tmp_path):
        """출력 JSONL에 이미 있는 report_id는 skip."""
        input_path = tmp_path / "inputs.jsonl"
        output_path = tmp_path / "outputs.jsonl"

        items = [{"report_id": i, "user_content": f"c{i}"} for i in range(1, 6)]
        input_path.write_text("\n".join(json.dumps(i) for i in items), encoding="utf-8")

        # report_id 1, 2, 3 이미 처리됨
        existing = [
            {"report_id": 1, "status": "success"},
            {"report_id": 2, "status": "success"},
            {"report_id": 3, "status": "failed"},
        ]
        output_path.write_text("\n".join(json.dumps(r) for r in existing), encoding="utf-8")

        valid = _make_valid_layer2()
        model_text = f"```json\n{json.dumps(valid)}\n```"
        success_stdout = _claude_json_wrapper(model_text)

        processed_ids = []
        original_process = _process_one

        def _mock_process(item, timeout, claude_cmd="claude", cwd=None, model=None):
            processed_ids.append(item["report_id"])
            return original_process.__wrapped__(item, timeout, claude_cmd) if hasattr(original_process, "__wrapped__") else {
                "report_id": item["report_id"],
                "result": _make_valid_layer2(),
                "sanitize_log": [],
                "status": "success",
                "elapsed_sec": 0.1,
            }

        args = SimpleNamespace(
            input=str(input_path),
            output=str(output_path),
            concurrency=1,
            max_daily=0,
            timeout=30,
            claude_cmd="claude",
        )

        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=success_stdout)), \
             patch("scripts.claude_layer2._process_one", side_effect=_mock_process):
            run(args)

        # 1,2는 success로 skip, 3은 failed이므로 재시도 → 3,4,5 처리
        assert set(processed_ids) == {3, 4, 5}

    def test_all_already_done_no_processing(self, tmp_path):
        """모두 처리된 경우 subprocess 호출 없음."""
        input_path = tmp_path / "inputs.jsonl"
        output_path = tmp_path / "outputs.jsonl"

        items = [{"report_id": 1, "user_content": "c1"}]
        input_path.write_text(json.dumps(items[0]), encoding="utf-8")
        output_path.write_text(json.dumps({"report_id": 1, "status": "success"}), encoding="utf-8")

        args = SimpleNamespace(
            input=str(input_path),
            output=str(output_path),
            concurrency=1,
            max_daily=0,
            timeout=30,
            claude_cmd="claude",
        )

        with patch("subprocess.run") as mock_run:
            run(args)
            mock_run.assert_not_called()


# ── 출력 JSONL 형식 검증 ──────────────────────────────────────────────────────

class TestOutputFormat:
    def test_success_record_format(self, tmp_path):
        """성공 레코드 형식: report_id, result, sanitize_log, status, elapsed_sec."""
        input_path = tmp_path / "inputs.jsonl"
        output_path = tmp_path / "outputs.jsonl"

        items = [{"report_id": 42, "user_content": "test content"}]
        input_path.write_text(json.dumps(items[0]), encoding="utf-8")

        valid = _make_valid_layer2()
        model_text = f"```json\n{json.dumps(valid)}\n```"
        success_stdout = _claude_json_wrapper(model_text)

        args = SimpleNamespace(
            input=str(input_path),
            output=str(output_path),
            concurrency=1,
            max_daily=0,
            timeout=30,
            claude_cmd="claude",
        )

        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=success_stdout)):
            run(args)

        records = [json.loads(l) for l in output_path.read_text(encoding="utf-8").strip().splitlines()]
        assert len(records) == 1
        rec = records[0]

        assert rec["report_id"] == 42
        assert rec["status"] == "success"
        assert isinstance(rec["result"], dict)
        assert isinstance(rec["sanitize_log"], list)
        assert isinstance(rec["elapsed_sec"], float)

    def test_failed_record_format(self, tmp_path):
        """실패 레코드 형식: report_id, result=null, error, status, elapsed_sec."""
        input_path = tmp_path / "inputs.jsonl"
        output_path = tmp_path / "outputs.jsonl"

        items = [{"report_id": 99, "user_content": "test"}]
        input_path.write_text(json.dumps(items[0]), encoding="utf-8")

        args = SimpleNamespace(
            input=str(input_path),
            output=str(output_path),
            concurrency=1,
            max_daily=0,
            timeout=30,
            claude_cmd="claude",
        )

        with patch("subprocess.run", return_value=_make_subprocess_result(stdout="not json")):
            run(args)

        records = [json.loads(l) for l in output_path.read_text().strip().splitlines()]
        assert len(records) == 1
        rec = records[0]

        assert rec["report_id"] == 99
        assert rec["status"] == "failed"
        assert rec["result"] is None
        assert "error" in rec
        assert isinstance(rec["elapsed_sec"], float)

    def test_each_line_is_valid_json(self, tmp_path):
        """출력 JSONL의 각 줄이 유효한 JSON."""
        input_path = tmp_path / "inputs.jsonl"
        output_path = tmp_path / "outputs.jsonl"

        items = [{"report_id": i, "user_content": f"c{i}"} for i in range(1, 4)]
        input_path.write_text("\n".join(json.dumps(i) for i in items), encoding="utf-8")

        valid = _make_valid_layer2()
        model_text = f"```json\n{json.dumps(valid)}\n```"
        success_stdout = _claude_json_wrapper(model_text)

        args = SimpleNamespace(
            input=str(input_path),
            output=str(output_path),
            concurrency=1,
            max_daily=0,
            timeout=30,
            claude_cmd="claude",
        )

        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=success_stdout)):
            run(args)

        lines = output_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3
        for line in lines:
            parsed = json.loads(line)
            assert isinstance(parsed, dict)

    def test_concurrency_produces_correct_count(self, tmp_path):
        """concurrency=3으로 5건 처리 시 5건 출력."""
        input_path = tmp_path / "inputs.jsonl"
        output_path = tmp_path / "outputs.jsonl"

        items = [{"report_id": i, "user_content": f"c{i}"} for i in range(1, 6)]
        input_path.write_text("\n".join(json.dumps(i) for i in items), encoding="utf-8")

        valid = _make_valid_layer2()
        model_text = f"```json\n{json.dumps(valid)}\n```"
        success_stdout = _claude_json_wrapper(model_text)

        args = SimpleNamespace(
            input=str(input_path),
            output=str(output_path),
            concurrency=3,
            max_daily=0,
            timeout=30,
            claude_cmd="claude",
        )

        with patch("subprocess.run", return_value=_make_subprocess_result(stdout=success_stdout)):
            run(args)

        lines = output_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 5


# ── _build_prompt 테스트 ──────────────────────────────────────────────────────

class TestBuildPrompt:
    def test_contains_system_prompt(self):
        from parser.layer2_extractor import _SYSTEM_PROMPT
        prompt = _build_prompt("리포트 내용")
        assert _SYSTEM_PROMPT in prompt

    def test_contains_user_content(self):
        prompt = _build_prompt("특별한 리포트 내용")
        assert "특별한 리포트 내용" in prompt

    def test_contains_schema(self):
        prompt = _build_prompt("내용")
        assert "report_category" in prompt
        assert "chain" in prompt

    def test_contains_json_instruction(self):
        prompt = _build_prompt("내용")
        assert "json" in prompt.lower()


# ── Fix 4: prompt passed via stdin, not CLI arg ───────────────────────────────

class TestPromptViaStdin:
    """Regression: prompt must be sent via stdin (input=) not as a positional CLI arg,
    to avoid Windows 32KB CreateProcess command-line limit for large reports."""

    def _item(self, report_id=1, user_content="리포트 내용"):
        return {"report_id": report_id, "user_content": user_content}

    def test_subprocess_called_with_input_not_positional_prompt(self):
        """subprocess.run must receive `input=prompt` and NOT include the prompt in args."""
        valid = _make_valid_layer2()
        model_text = f"```json\n{json.dumps(valid, ensure_ascii=False)}\n```"
        stdout = _claude_json_wrapper(model_text)

        captured_calls = []

        def _mock_run(*args, **kwargs):
            captured_calls.append({"args": args, "kwargs": kwargs})
            return _make_subprocess_result(stdout=stdout)

        item = self._item(user_content="테스트 리포트 내용입니다")
        prompt = _build_prompt(item["user_content"])

        with patch("subprocess.run", side_effect=_mock_run):
            result = _process_one(item, timeout=30)

        assert result["status"] == "success"
        assert len(captured_calls) == 1

        call_kwargs = captured_calls[0]["kwargs"]
        call_args_positional = captured_calls[0]["args"]

        # The prompt must arrive as `input=` kwarg
        assert "input" in call_kwargs, "prompt must be passed as input= kwarg (stdin), not CLI arg"
        assert call_kwargs["input"] == prompt

        # The prompt must NOT be a positional argument in the command list
        cmd_list = call_args_positional[0] if call_args_positional else call_kwargs.get("args", [])
        assert prompt not in cmd_list, (
            "prompt must not appear as a CLI positional argument — it must be sent via stdin"
        )

    def test_subprocess_cmd_does_not_contain_prompt_text(self):
        """The command list passed to subprocess must not contain the full prompt string."""
        valid = _make_valid_layer2()
        model_text = f"```json\n{json.dumps(valid)}\n```"
        stdout = _claude_json_wrapper(model_text)

        # Use a long user_content to simulate a large report
        long_content = "X" * 1000
        item = self._item(user_content=long_content)

        captured_cmd = []

        def _mock_run(*args, **kwargs):
            captured_cmd.extend(args[0] if args else [])
            return _make_subprocess_result(stdout=stdout)

        with patch("subprocess.run", side_effect=_mock_run):
            _process_one(item, timeout=30)

        # None of the cmd tokens should be the prompt (which contains the long content)
        for token in captured_cmd:
            assert long_content not in str(token), (
                f"Prompt content found in CLI args — should be passed via stdin instead"
            )
