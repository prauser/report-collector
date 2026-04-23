"""Layer2 분석을 `claude -p` CLI로 처리하는 스크립트.

Anthropic Batch API 대신 Claude Code Max 플랜의 `claude -p` subprocess를 사용.
입력: run_analysis.py --dump-layer2 로 생성된 JSONL
출력: 건별 Layer2 분석 결과 JSONL

사용법:
  python scripts/claude_layer2.py
  python scripts/claude_layer2.py --input data/layer2_inputs.jsonl --output data/layer2_outputs.jsonl
  python scripts/claude_layer2.py --concurrency 4 --max-daily 500
  python scripts/claude_layer2.py --timeout 180
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
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

# repo root를 path에 추가 (scripts/ 에서 실행 시)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config.settings import settings
from parser.layer2_extractor import _SYSTEM_PROMPT, _EXTRACT_TOOL
from parser.layer2_validator import validate_and_sanitize_layer2

# ── 상수 ──────────────────────────────────────────────────────────────────────

_DEFAULT_INPUT = Path(_ROOT) / "data" / "layer2_inputs.jsonl"
_DEFAULT_OUTPUT = Path(_ROOT) / "data" / "layer2_outputs.jsonl"

# Rate limit backoff 설정
_BACKOFF_INITIAL = 30   # seconds
_BACKOFF_MAX = 300      # 5 minutes
_BACKOFF_RETRIES = 3

# 스키마 문자열 (프롬프트에 포함)
_SCHEMA_JSON = json.dumps(_EXTRACT_TOOL["input_schema"], ensure_ascii=False, indent=2)

# ── 프롬프트 빌더 ─────────────────────────────────────────────────────────────

def _build_prompt(user_content: str) -> str:
    """claude -p 에 전달할 전체 프롬프트 생성."""
    return (
        f"{_SYSTEM_PROMPT}\n\n"
        "## 출력 형식\n"
        "아래 JSON 스키마에 맞는 JSON 객체를 ```json 코드 블록으로 응답하세요.\n"
        "스키마 외 필드는 포함하지 마세요.\n\n"
        f"```json-schema\n{_SCHEMA_JSON}\n```\n\n"
        "## 분석 대상 리포트\n"
        f"{user_content}"
    )


# ── JSON 파싱 ─────────────────────────────────────────────────────────────────

def _parse_json_from_result(result_text: str) -> dict | None:
    """claude 응답 텍스트에서 JSON 추출.

    1) ```json ... ``` 코드 블록 우선
    2) 직접 JSON 파싱 시도
    """
    # 1) ```json ... ``` 블록
    match = re.search(r"```json\s*\n?(.*?)```", result_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass

    # 2) ``` 블록 (언어 태그 없음)
    match = re.search(r"```\s*\n?(\{.*?\})\s*```", result_text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except (json.JSONDecodeError, ValueError):
            pass

    # 3) 직접 JSON 파싱 (전체 텍스트가 JSON인 경우)
    try:
        return json.loads(result_text.strip())
    except (json.JSONDecodeError, ValueError):
        pass

    # 4) 텍스트에서 { ... } 추출 (최외곽 중괄호)
    start = result_text.find("{")
    end = result_text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(result_text[start : end + 1])
        except (json.JSONDecodeError, ValueError):
            pass

    return None


# ── Rate limit 감지 ───────────────────────────────────────────────────────────

def _is_rate_limited(returncode: int, stderr: str) -> bool:
    """subprocess 결과에서 rate limit 감지."""
    if returncode == 0:
        return False
    lower_stderr = stderr.lower()
    return "429" in stderr or "rate_limit" in lower_stderr or "rate limit" in lower_stderr


# ── 단건 처리 ─────────────────────────────────────────────────────────────────

def _process_one(
    item: dict,
    timeout: int,
    claude_cmd: str = "claude",
    cwd: str | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> dict:
    """단일 JSONL 항목을 claude -p 로 처리.

    Returns:
        result dict (출력 JSONL 한 줄분)
    """
    report_id = item.get("report_id")
    user_content = item.get("user_content", "")
    prompt = _build_prompt(user_content)

    t_start = time.perf_counter()
    backoff = _BACKOFF_INITIAL

    # Windows에서 npm 글로벌 바이너리(.cmd)를 찾기 위해 shutil.which로 절대경로 resolve
    # 절대경로 사용 시 shell=False로 충분 (cwd 변경 시에도 안전)
    resolved_cmd = shutil.which(claude_cmd) or claude_cmd

    for attempt in range(_BACKOFF_RETRIES + 1):
        try:
            cmd_args = [resolved_cmd, "-p", "--output-format", "json"]
            if model:
                cmd_args.extend(["--model", model])
            if effort:
                cmd_args.extend(["--effort", effort])
            proc = subprocess.run(
                cmd_args,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=cwd,
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
                "error": "claude_not_found",
                "status": "failed",
                "elapsed_sec": elapsed,
            }

        # rate limit 체크
        if _is_rate_limited(proc.returncode, proc.stderr):
            if attempt < _BACKOFF_RETRIES:
                print(
                    f"  [rate_limit] report_id={report_id} "
                    f"backoff={backoff}s (attempt {attempt + 1}/{_BACKOFF_RETRIES})",
                    flush=True,
                )
                time.sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)
                continue
            else:
                elapsed = round(time.perf_counter() - t_start, 2)
                return {
                    "report_id": report_id,
                    "result": None,
                    "error": "rate_limit_exhausted",
                    "status": "failed",
                    "elapsed_sec": elapsed,
                }

        # 비정상 종료 (rate limit 아닌 에러)
        if proc.returncode != 0:
            elapsed = round(time.perf_counter() - t_start, 2)
            stderr_tail = (proc.stderr or "").strip()[-300:]  # 마지막 300자
            return {
                "report_id": report_id,
                "result": None,
                "error": f"subprocess_error:{proc.returncode}",
                "stderr": stderr_tail,
                "status": "failed",
                "elapsed_sec": elapsed,
            }

        # 정상 종료 — stdout 파싱
        # --output-format json: CLI wrapper {"type":"result","result":"...","session_id":"..."}
        raw_stdout = proc.stdout.strip()
        model_text: str | None = None

        try:
            wrapper = json.loads(raw_stdout)
            if isinstance(wrapper, dict) and "result" in wrapper:
                model_text = wrapper["result"]
            else:
                # wrapper 자체가 모델 응답인 경우
                model_text = raw_stdout
        except (json.JSONDecodeError, ValueError):
            model_text = raw_stdout

        parsed = _parse_json_from_result(model_text) if model_text else None
        elapsed = round(time.perf_counter() - t_start, 2)

        if parsed is None:
            return {
                "report_id": report_id,
                "result": None,
                "error": "json_parse_failed",
                "status": "failed",
                "elapsed_sec": elapsed,
            }

        # validate & sanitize
        sanitized, corrections = validate_and_sanitize_layer2(parsed, report_id=report_id)
        if sanitized is None:
            return {
                "report_id": report_id,
                "result": None,
                "sanitize_log": corrections,
                "error": "validation_failed",
                "status": "failed",
                "elapsed_sec": elapsed,
            }

        return {
            "report_id": report_id,
            "result": sanitized,
            "sanitize_log": corrections,
            "status": "success",
            "elapsed_sec": elapsed,
        }

    # should not reach here
    elapsed = round(time.perf_counter() - t_start, 2)
    return {
        "report_id": report_id,
        "result": None,
        "error": "unknown",
        "status": "failed",
        "elapsed_sec": elapsed,
    }


# ── 입력 로더 ─────────────────────────────────────────────────────────────────

def _load_inputs(input_path: Path) -> list[dict]:
    """JSONL 파일에서 입력 항목 로드."""
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
    """pending_batches.jsonl에서 배치 제출된 report_id set 로드."""
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
                        # "report-12345" → 12345
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
    """출력 JSONL에서 이미 처리된 report_id set 로드 (resume 로직)."""
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
                # 성공 건만 skip — 실패 건은 재시도 가능
                if rec.get("status") == "success":
                    rid = rec.get("report_id")
                    if rid is not None:
                        done.add(rid)
            except (json.JSONDecodeError, ValueError):
                pass
    return done


# ── 출력 쓰기 ─────────────────────────────────────────────────────────────────

class _OutputWriter:
    """Thread-safe JSONL append writer."""

    def __init__(self, output_path: Path) -> None:
        self._path = output_path
        self._lock = Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False)
        with self._lock:
            with open(self._path, "a", encoding="utf-8") as f:
                f.write(line + "\n")


# ── 메인 ─────────────────────────────────────────────────────────────────────

def run(args: argparse.Namespace) -> None:
    input_path = Path(args.input)
    output_path = Path(args.output)
    concurrency: int = args.concurrency
    max_daily: int = args.max_daily
    timeout: int = args.timeout
    claude_cmd: str = getattr(args, "claude_cmd", "claude")
    cwd: str | None = getattr(args, "cwd", None)
    model: str | None = getattr(args, "model", None)
    effort: str | None = getattr(args, "effort", None)

    print(f"=== claude_layer2.py ===")
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

    # 입력 로드
    all_items = _load_inputs(input_path)
    print(f"입력 총 {len(all_items)}건 로드")

    # resume: 이미 처리된 ID skip
    done_ids = _load_done_ids(output_path)
    if done_ids:
        print(f"Resume: {len(done_ids)}건 이미 처리됨 → skip")

    # pending_batches.jsonl에 이미 배치 제출된 report ID 제외
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

    # daily limit 적용
    if max_daily > 0 and len(pending) > max_daily:
        print(f"--max-daily={max_daily} 적용: {len(pending)}건 → {max_daily}건으로 제한")
        pending = pending[:max_daily]

    writer = _OutputWriter(output_path)

    total = len(pending)
    success_count = 0
    fail_count = 0
    processed_count = 0
    _count_lock = Lock()

    def _worker(idx_item: tuple[int, dict]) -> None:
        nonlocal success_count, fail_count, processed_count
        idx, item = idx_item
        report_id = item.get("report_id")

        result = _process_one(item, timeout=timeout, claude_cmd=claude_cmd, cwd=cwd, model=model, effort=effort)
        writer.append(result)

        with _count_lock:
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
    parser = argparse.ArgumentParser(
        description="Layer2 분석을 claude -p CLI로 처리"
    )
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
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="동시 subprocess 수 (기본: 1)",
    )
    parser.add_argument(
        "--max-daily",
        type=int,
        default=0,
        help="일일 최대 처리 건수 (기본: 0=무제한)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="건당 타임아웃 초 (기본: 120)",
    )
    parser.add_argument(
        "--claude-cmd",
        default="claude",
        help="claude CLI 명령어 경로 (기본: claude)",
    )
    parser.add_argument(
        "--model",
        default="claude-sonnet-4-6",
        help="claude -p에 전달할 모델 (기본: claude-sonnet-4-6)",
    )
    parser.add_argument(
        "--effort",
        default=None,
        choices=["low", "medium", "high", "xhigh", "max"],
        help="claude -p에 전달할 effort level (기본: 모델 기본값)",
    )
    parser.add_argument(
        "--cwd",
        default=str(settings.pdf_base_path),
        help=f"claude -p subprocess 작업 디렉토리 (기본: {settings.pdf_base_path}). 세션 목록 분리용",
    )
    return parser


if __name__ == "__main__":
    _parser = _build_arg_parser()
    _args = _parser.parse_args()
    run(_args)
