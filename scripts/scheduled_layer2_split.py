"""Scheduled Layer2 runner that splits one dump across Claude and Codex.

Default flow:
  1. Dump 300 pending Layer2 inputs once.
  2. Send the first 100 rows to claude_layer2.py.
  3. Send the next 200 rows to codex_layer2.py.
  4. Import both output files.

Claude model selection is controlled by args/env so the existing Sonnet/Opus
quota-switch workflow can reuse the same split runner.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

_ROOT = Path(__file__).resolve().parent.parent


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(f"[{_ts()}] {message}\n")


def _remove_if_exists(path: Path) -> None:
    if path.exists():
        path.unlink()


def _run_logged(name: str, cmd: list[str], cwd: Path, log_path: Path) -> int:
    _log(log_path, f"{name} START: {' '.join(cmd)}")
    with open(log_path, "a", encoding="utf-8", errors="replace") as f:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            stdout=f,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    _log(log_path, f"{name} END exit={proc.returncode}")
    return proc.returncode


def _is_task_running(task_name: str) -> bool:
    proc = subprocess.run(
        ["schtasks", "/Query", "/TN", task_name, "/FO", "LIST", "/V"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        return False
    for line in proc.stdout.splitlines():
        if line.strip().lower().startswith("status:"):
            return "running" in line.lower()
    return False


def _wait_for_tasks(task_names: list[str], timeout_sec: int, log_path: Path) -> bool:
    if not task_names:
        return True
    deadline = time.monotonic() + timeout_sec
    while True:
        running = [name for name in task_names if _is_task_running(name)]
        if not running:
            return True
        if time.monotonic() >= deadline:
            _log(log_path, f"wait timeout; still running: {', '.join(running)}")
            return False
        _log(log_path, f"waiting for running tasks to finish: {', '.join(running)}")
        time.sleep(60)


def _split_dump(
    dump_path: Path,
    claude_input: Path,
    codex_input: Path,
    claude_limit: int,
    codex_limit: int,
) -> tuple[int, int, int]:
    if not dump_path.exists():
        return 0, 0, 0

    lines = [
        line
        for line in dump_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    claude_lines = lines[:claude_limit]
    codex_lines = lines[claude_limit : claude_limit + codex_limit]

    claude_input.parent.mkdir(parents=True, exist_ok=True)
    codex_input.parent.mkdir(parents=True, exist_ok=True)
    claude_input.write_text(
        "\n".join(claude_lines) + ("\n" if claude_lines else ""),
        encoding="utf-8",
    )
    codex_input.write_text(
        "\n".join(codex_lines) + ("\n" if codex_lines else ""),
        encoding="utf-8",
    )
    return len(lines), len(claude_lines), len(codex_lines)


def _start_logged(name: str, cmd: list[str], cwd: Path, log_path: Path):
    _log(log_path, f"{name} START: {' '.join(cmd)}")
    handle = open(log_path, "a", encoding="utf-8", errors="replace")
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc, handle


def _build_claude_cmd(args: argparse.Namespace, python: Path, input_path: Path, output_path: Path) -> list[str]:
    cmd = [
        str(python),
        "scripts\\claude_layer2.py",
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--concurrency",
        str(args.claude_concurrency),
        "--timeout",
        str(args.claude_timeout),
    ]
    if args.claude_model:
        cmd.extend(["--model", args.claude_model])
    if args.claude_effort:
        cmd.extend(["--effort", args.claude_effort])
    return cmd


def run(args: argparse.Namespace) -> int:
    python = Path(args.python)
    dump_path = Path(args.dump_path)
    claude_input = Path(args.claude_input)
    codex_input = Path(args.codex_input)
    claude_output = Path(args.claude_output)
    codex_output = Path(args.codex_output)
    log_path = Path(args.log_file)
    claude_log_path = Path(args.claude_log_file)
    codex_log_path = Path(args.codex_log_file)

    if not python.exists():
        _log(log_path, f"FATAL python not found: {python}")
        return 1

    _log(log_path, "========================================")
    _log(
        log_path,
        "Layer2 split schedule START "
        f"(total={args.total_limit}, claude={args.claude_limit}, codex={args.codex_limit}, "
        f"claude_concurrency={args.claude_concurrency}, codex_concurrency={args.codex_concurrency}, "
        f"claude_model={args.claude_model or '(default)'}, "
        f"claude_effort={args.claude_effort or '(default)'})",
    )

    if not _wait_for_tasks(args.wait_task, args.wait_timeout, log_path):
        _log(log_path, "Previous task still running. Stop before dump to avoid duplicate processing.")
        return 0

    for path in (dump_path, claude_input, codex_input):
        _remove_if_exists(path)

    dump_rc = _run_logged(
        "dump",
        [
            str(python),
            "run_analysis.py",
            "--dump-layer2",
            "--dump-layer2-path",
            str(dump_path),
            "--limit",
            str(args.total_limit),
        ],
        _ROOT,
        log_path,
    )
    if dump_rc != 0:
        _log(log_path, f"dump failed; stopping. exit={dump_rc}")
        return dump_rc

    total, claude_count, codex_count = _split_dump(
        dump_path,
        claude_input,
        codex_input,
        args.claude_limit,
        args.codex_limit,
    )
    _log(log_path, f"split complete: total={total}, claude={claude_count}, codex={codex_count}")

    if total == 0:
        _log(log_path, "No dump records. Nothing to process.")
        return 0

    processors = []
    if claude_count:
        processors.append(
            (
                "claude",
                *_start_logged(
                    "claude_layer2",
                    _build_claude_cmd(args, python, claude_input, claude_output),
                    _ROOT,
                    claude_log_path,
                ),
            )
        )
    if codex_count:
        processors.append(
            (
                "codex",
                *_start_logged(
                    "codex_layer2",
                    [
                        str(python),
                        "scripts\\codex_layer2.py",
                        "--input",
                        str(codex_input),
                        "--output",
                        str(codex_output),
                        "--concurrency",
                        str(args.codex_concurrency),
                        "--max-daily",
                        str(args.codex_limit),
                        "--timeout",
                        str(args.codex_timeout),
                    ],
                    _ROOT,
                    codex_log_path,
                ),
            )
        )

    processor_rc = 0
    for name, proc, handle in processors:
        rc = proc.wait()
        handle.close()
        _log(log_path, f"{name} processor exit={rc}")
        processor_rc = processor_rc or rc

    import_rc = 0
    if claude_count:
        import_rc = import_rc or _run_logged(
            "import_claude",
            [str(python), "scripts\\import_layer2.py", "--input", str(claude_output), "--apply"],
            _ROOT,
            log_path,
        )
    if codex_count:
        import_rc = import_rc or _run_logged(
            "import_codex",
            [str(python), "scripts\\import_layer2.py", "--input", str(codex_output), "--apply"],
            _ROOT,
            log_path,
        )

    final_rc = processor_rc or import_rc
    _log(log_path, f"Layer2 split schedule END exit={final_rc}")
    _log(log_path, "========================================")
    return final_rc


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(name) or default


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scheduled split Layer2 runner for Claude and Codex")
    parser.add_argument("--python", default=str(_ROOT / ".venv" / "Scripts" / "python.exe"))
    parser.add_argument("--total-limit", type=int, default=int(_env("LAYER2_SPLIT_TOTAL_LIMIT", "300")))
    parser.add_argument("--claude-limit", type=int, default=int(_env("LAYER2_SPLIT_CLAUDE_LIMIT", "100")))
    parser.add_argument("--codex-limit", type=int, default=int(_env("LAYER2_SPLIT_CODEX_LIMIT", "200")))
    parser.add_argument("--claude-timeout", type=int, default=int(_env("LAYER2_SPLIT_CLAUDE_TIMEOUT", "180")))
    parser.add_argument("--codex-timeout", type=int, default=int(_env("LAYER2_SPLIT_CODEX_TIMEOUT", "240")))
    parser.add_argument("--claude-concurrency", type=int, default=int(_env("LAYER2_SPLIT_CLAUDE_CONCURRENCY", "1")))
    parser.add_argument("--codex-concurrency", type=int, default=int(_env("LAYER2_SPLIT_CODEX_CONCURRENCY", "3")))
    parser.add_argument("--claude-model", default=_env("LAYER2_SPLIT_CLAUDE_MODEL", "claude-sonnet-4-6"))
    parser.add_argument("--claude-effort", default=_env("LAYER2_SPLIT_CLAUDE_EFFORT"))
    parser.add_argument("--wait-task", action="append", default=["Layer2_Codex_Code"])
    parser.add_argument("--wait-timeout", type=int, default=int(_env("LAYER2_SPLIT_WAIT_TIMEOUT", "14400")))
    parser.add_argument("--dump-path", default=str(_ROOT / "data" / "layer2_split_scheduled_inputs.jsonl"))
    parser.add_argument("--claude-input", default=str(_ROOT / "data" / "layer2_split_claude_inputs.jsonl"))
    parser.add_argument("--codex-input", default=str(_ROOT / "data" / "layer2_split_codex_inputs.jsonl"))
    parser.add_argument("--claude-output", default=str(_ROOT / "data" / "layer2_scheduled_outputs.jsonl"))
    parser.add_argument("--codex-output", default=str(_ROOT / "data" / "layer2_codex_scheduled_outputs.jsonl"))
    parser.add_argument("--log-file", default=str(_ROOT / "logs" / "scheduled_layer2_split.log"))
    parser.add_argument("--claude-log-file", default=str(_ROOT / "logs" / "scheduled_layer2_split_claude.log"))
    parser.add_argument("--codex-log-file", default=str(_ROOT / "logs" / "scheduled_layer2_split_codex.log"))
    return parser


if __name__ == "__main__":
    sys.exit(run(_build_arg_parser().parse_args()))
