"""Layer2 분석을 `codex exec` CLI로 처리하는 스크립트.

Anthropic Batch API나 Claude Code CLI 대신 Codex 구독/CLI의 비대화형
실행 모드를 사용한다. 입출력 JSONL 계약은 scripts/claude_layer2.py와
동일하게 유지하여 scripts/import_layer2.py를 그대로 재사용할 수 있다.

사용법:
  python scripts/codex_layer2.py --input data/layer2_inputs.jsonl --output data/layer2_codex_outputs.jsonl
  python scripts/codex_layer2.py --concurrency 1 --max-daily 10 --timeout 240
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config.settings import settings
from parser.layer2_extractor import _SYSTEM_PROMPT, _EXTRACT_TOOL
from parser.layer2_validator import validate_and_sanitize_layer2

_DEFAULT_INPUT = Path(_ROOT) / "data" / "layer2_inputs.jsonl"
_DEFAULT_OUTPUT = Path(_ROOT) / "data" / "layer2_codex_outputs.jsonl"

_BACKOFF_INITIAL = 30
_BACKOFF_MAX = 300
_BACKOFF_RETRIES = 3

_SCHEMA = _EXTRACT_TOOL["input_schema"]
_SCHEMA_JSON = json.dumps(_SCHEMA, ensure_ascii=False, indent=2)


def _build_prompt(user_content: str) -> str:
    """codex exec stdin으로 전달할 전체 프롬프트 생성."""
    return (
        f"{_SYSTEM_PROMPT}\n\n"
        "## 작업 지시\n"
        "당신은 코드 작성 에이전트가 아니라 리포트 분석기로만 동작해야 합니다.\n"
        "저장소 파일을 탐색하거나 명령을 실행하지 말고, 아래 리포트 본문만 분석하세요.\n"
        "최종 답변은 제공된 JSON Schema를 만족하는 JSON 객체 하나여야 합니다.\n"
        "마크다운 코드블록, 설명문, 주석, 스키마 외 필드는 포함하지 마세요.\n\n"
        "## 참고 스키마\n"
        f"```json\n{_SCHEMA_JSON}\n```\n\n"
        "## 분석 대상 리포트\n"
        f"{user_content}"
    )


def _parse_json_from_result(result_text: str) -> dict | None:
    """Codex 응답 텍스트에서 JSON 객체 추출."""
    match = re.search(r"```json\s*\n?(.*?)```", result_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass

    match = re.search(r"```\s*\n?(\{.*?\})\s*```", result_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass

    try:
        return json.loads(result_text.strip())
    except (json.JSONDecodeError, ValueError):
        pass

    start = result_text.find("{")
    end = result_text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(result_text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            pass

    return None


def _is_rate_limited(returncode: int, stderr: str) -> bool:
    if returncode == 0:
        return False
    lower = (stderr or "").lower()
    if "invalid_json_schema" in lower or "invalid_request_error" in lower:
        return False
    return (
        re.search(r"\b429\b", lower) is not None
        or "rate_limit" in lower
        or "rate limit" in lower
        or "too many requests" in lower
        or "usage limit" in lower
    )


def _read_codex_result(output_path: Path, stdout: str) -> dict | None:
    """-o 출력 파일을 우선 읽고, 없으면 stdout을 fallback으로 파싱."""
    if output_path.exists():
        text = output_path.read_text(encoding="utf-8", errors="replace").strip()
        if text:
            parsed = _parse_json_from_result(text)
            if parsed is not None:
                return parsed
    return _parse_json_from_result(stdout or "")


def _parse_codex_tokens_used(stdout: str) -> int | None:
    """codex exec stdout의 'tokens used' 블록에서 총 토큰 수 추출."""
    if not stdout:
        return None
    match = re.search(r"tokens used\s*\r?\n\s*([0-9][0-9,]*)", stdout, re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1).replace(",", ""))
    except ValueError:
        return None


def _process_one(
    item: dict,
    timeout: int,
    codex_cmd: str = "codex",
    cwd: str | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> dict:
    """단일 JSONL 항목을 codex exec로 처리."""
    report_id = item.get("report_id")
    user_content = item.get("user_content", "")
    prompt = _build_prompt(user_content)

    t_start = time.perf_counter()
    backoff = _BACKOFF_INITIAL
    resolved_cmd = shutil.which(codex_cmd) or codex_cmd

    for attempt in range(_BACKOFF_RETRIES + 1):
        last_stderr = ""
        with tempfile.TemporaryDirectory(prefix="codex-layer2-") as tmp_dir:
            result_path = Path(tmp_dir) / "result.json"

            cmd_args = [
                resolved_cmd,
                "exec",
                "-",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "-o",
                str(result_path),
            ]
            if model:
                cmd_args.extend(["--model", model])
            if effort:
                cmd_args.extend(["-c", f"model_reasoning_effort={effort}"])
            if cwd:
                cmd_args.extend(["--cd", cwd])

            try:
                proc = subprocess.run(
                    cmd_args,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout,
                )
            except subprocess.TimeoutExpired:
                elapsed = round(time.perf_counter() - t_start, 2)
                return {
                    "report_id": report_id,
                    "result": None,
                    "error": "timeout",
                    "status": "failed",
                    "elapsed_sec": elapsed,
                }
            except FileNotFoundError:
                elapsed = round(time.perf_counter() - t_start, 2)
                return {
                    "report_id": report_id,
                    "result": None,
                    "error": "codex_not_found",
                    "status": "failed",
                    "elapsed_sec": elapsed,
                }

            if _is_rate_limited(proc.returncode, proc.stderr):
                last_stderr = (proc.stderr or "").strip()[-500:]
                if attempt < _BACKOFF_RETRIES:
                    print(
                        f"  [rate_limit] report_id={report_id} "
                        f"backoff={backoff}s (attempt {attempt + 1}/{_BACKOFF_RETRIES})",
                        flush=True,
                    )
                    time.sleep(backoff)
                    backoff = min(backoff * 2, _BACKOFF_MAX)
                    continue

                elapsed = round(time.perf_counter() - t_start, 2)
                return {
                    "report_id": report_id,
                    "result": None,
                    "error": "rate_limit_exhausted",
                    "stderr": last_stderr,
                    "status": "failed",
                    "elapsed_sec": elapsed,
                }

            if proc.returncode != 0:
                elapsed = round(time.perf_counter() - t_start, 2)
                stderr_tail = (proc.stderr or "").strip()[-500:]
                return {
                    "report_id": report_id,
                    "result": None,
                    "error": f"subprocess_error:{proc.returncode}",
                    "stderr": stderr_tail,
                    "status": "failed",
                    "elapsed_sec": elapsed,
                }

            parsed = _read_codex_result(result_path, proc.stdout)
            elapsed = round(time.perf_counter() - t_start, 2)

            if parsed is None:
                return {
                    "report_id": report_id,
                    "result": None,
                    "error": "json_parse_failed",
                    "status": "failed",
                    "elapsed_sec": elapsed,
                }

            sanitized, corrections = validate_and_sanitize_layer2(parsed, report_id=report_id)
            tokens_used = _parse_codex_tokens_used((proc.stdout or "") + "\n" + (proc.stderr or ""))
            if sanitized is None:
                return {
                    "report_id": report_id,
                    "result": None,
                    "sanitize_log": corrections,
                    "error": "validation_failed",
                    "codex_tokens_used": tokens_used,
                    "status": "failed",
                    "elapsed_sec": elapsed,
                }

            return {
                "report_id": report_id,
                "result": sanitized,
                "sanitize_log": corrections,
                "codex_tokens_used": tokens_used,
                "codex_model": model,
                "codex_effort": effort,
                "status": "success",
                "elapsed_sec": elapsed,
            }

    elapsed = round(time.perf_counter() - t_start, 2)
    return {
        "report_id": report_id,
        "result": None,
        "error": "unknown",
        "status": "failed",
        "elapsed_sec": elapsed,
    }


def _load_inputs(input_path: Path) -> list[dict]:
    items = []
    if not input_path.exists():
        print(f"[ERROR] Input file not found: {input_path}", file=sys.stderr)
        return items
    with open(input_path, encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except (json.JSONDecodeError, ValueError) as e:
                print(f"[WARN] Input line {lineno} parse error: {e}", file=sys.stderr)
    return items


_PENDING_BATCHES_PATH = Path(__file__).resolve().parent.parent / "logs" / "pending_batches.jsonl"


def _load_pending_batch_ids() -> set:
    ids: set = set()
    if not _PENDING_BATCHES_PATH.exists():
        return ids
    try:
        with open(_PENDING_BATCHES_PATH, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    for cid in entry.get("custom_ids", []):
                        if cid.startswith("report-"):
                            try:
                                ids.add(int(cid.split("-", 1)[1]))
                            except (ValueError, IndexError):
                                pass
                except (json.JSONDecodeError, ValueError):
                    pass
    except OSError:
        pass
    return ids


def _load_done_ids(output_path: Path) -> set:
    done: set = set()
    if not output_path.exists():
        return done
    with open(output_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                if rec.get("status") == "success":
                    rid = rec.get("report_id")
                    if rid is not None:
                        done.add(rid)
            except (json.JSONDecodeError, ValueError):
                pass
    return done


class _OutputWriter:
    def __init__(self, output_path: Path) -> None:
        self._path = output_path
        self._lock = Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")


def run(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    output_path = Path(args.output)
    concurrency: int = args.concurrency
    max_daily: int = args.max_daily
    timeout: int = args.timeout
    codex_cmd: str = getattr(args, "codex_cmd", "codex")
    cwd: str | None = getattr(args, "cwd", None)
    model: str | None = getattr(args, "model", None)
    effort: str | None = getattr(args, "effort", None)

    print("=== codex_layer2.py ===")
    print(f"input:       {input_path}")
    print(f"output:      {output_path}")
    print(f"concurrency: {concurrency}")
    print(f"max_daily:   {max_daily if max_daily > 0 else '무제한'}")
    print(f"timeout:     {timeout}s")
    print(f"model:       {model or '(default)'}")
    print(f"effort:      {effort or '(default)'}")
    if cwd:
        print(f"cwd:         {cwd}")
    print()

    all_items = _load_inputs(input_path)
    print(f"입력 총 {len(all_items)}건 로드")

    done_ids = _load_done_ids(output_path)
    if done_ids:
        print(f"Resume: {len(done_ids)}건 이미 처리됨 → skip")

    pending_batch_ids = _load_pending_batch_ids()
    if pending_batch_ids:
        print(f"Pending batches: {len(pending_batch_ids)}건 배치 제출됨 → skip")

    exclude_ids = done_ids | pending_batch_ids
    pending = [item for item in all_items if item.get("report_id") not in exclude_ids]
    skip_count = len(all_items) - len(pending)
    print(f"처리 대상: {len(pending)}건 (skip: {skip_count}건)")

    if not pending:
        print("처리할 항목 없음. 종료.")
        return

    if max_daily > 0 and len(pending) > max_daily:
        print(f"--max-daily={max_daily} 적용: {len(pending)}건 → {max_daily}건으로 제한")
        pending = pending[:max_daily]

    writer = _OutputWriter(output_path)
    total = len(pending)
    success_count = 0
    fail_count = 0
    processed_count = 0
    count_lock = Lock()

    def _worker(idx_item: tuple[int, dict]) -> None:
        nonlocal success_count, fail_count, processed_count
        _, item = idx_item
        report_id = item.get("report_id")

        result = _process_one(
            item,
            timeout=timeout,
            codex_cmd=codex_cmd,
            cwd=cwd,
            model=model,
            effort=effort,
        )
        writer.append(result)

        with count_lock:
            processed_count += 1
            if result["status"] == "success":
                success_count += 1
            else:
                fail_count += 1
            current = processed_count

        status_str = "OK" if result["status"] == "success" else f"FAIL({result.get('error', '?')})"
        print(
            f"[{current}/{total}] report_id={report_id} {status_str} "
            f"({result['elapsed_sec']}s)",
            flush=True,
        )

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(_worker, (idx, item)): idx
            for idx, item in enumerate(pending, 1)
        }
        for future in as_completed(futures):
            exc = future.exception()
            if exc:
                print(f"[ERROR] Worker exception: {exc}", file=sys.stderr)

    print()
    print("=== 완료 ===")
    print(f"성공: {success_count}건")
    print(f"실패: {fail_count}건")
    print(f"스킵: {skip_count}건")
    remaining = len(all_items) - skip_count - processed_count
    if remaining > 0:
        print(f"미처리 (daily limit): {remaining}건")


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Layer2 분석을 codex exec CLI로 처리")
    parser.add_argument(
        "--input",
        default=str(_DEFAULT_INPUT),
        help=f"입력 JSONL 경로 (기본: {_DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--output",
        default=str(_DEFAULT_OUTPUT),
        help=f"출력 JSONL 경로 (기본: {_DEFAULT_OUTPUT})",
    )
    parser.add_argument("--concurrency", type=int, default=1, help="동시 subprocess 수 (기본: 1)")
    parser.add_argument("--max-daily", type=int, default=0, help="최대 처리 건수 (기본: 0=무제한)")
    parser.add_argument("--timeout", type=int, default=240, help="건당 타임아웃 초 (기본: 240)")
    parser.add_argument("--codex-cmd", default="codex", help="codex CLI 명령어 경로 (기본: codex)")
    parser.add_argument("--model", default=None, help="codex exec에 전달할 모델 (기본: Codex CLI 설정값)")
    parser.add_argument(
        "--effort",
        default=None,
        choices=["low", "medium", "high", "xhigh"],
        help="codex exec model_reasoning_effort 설정 (기본: Codex CLI 설정값)",
    )
    parser.add_argument(
        "--cwd",
        default=str(Path(_ROOT)),
        help="codex exec 작업 디렉토리 (기본: repo root)",
    )
    return parser


if __name__ == "__main__":
    _parser = _build_arg_parser()
    _args = _parser.parse_args()
    run(_args)
