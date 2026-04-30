"""Wait for Layer2 scheduled tasks to finish, then run today's batch submit once."""
from __future__ import annotations

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_PATH = ROOT / "logs" / "batch_submit_after_layer2.log"
WAIT_TASKS = ["Layer2_Codex_Code", "Layer2_Claude_Codex_Split"]
WAIT_TIMEOUT_SEC = 12 * 60 * 60


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _log(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"[{_ts()}] {message}\n")


def _task_status(task_name: str) -> str | None:
    proc = subprocess.run(
        ["schtasks", "/Query", "/TN", task_name, "/FO", "LIST", "/V"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if line.strip().lower().startswith("status:"):
            return line.split(":", 1)[1].strip()
    return None


def _wait_for_layer2() -> bool:
    deadline = time.monotonic() + WAIT_TIMEOUT_SEC
    while True:
        running = [task for task in WAIT_TASKS if (_task_status(task) or "").lower() == "running"]
        if not running:
            _log("Layer2 tasks are idle")
            return True
        if time.monotonic() >= deadline:
            _log(f"timeout waiting for: {', '.join(running)}")
            return False
        _log(f"waiting for: {', '.join(running)}")
        time.sleep(60)


def _run_batch_submit() -> int:
    script = ROOT / "scripts" / "scheduled_batch_submit.bat"
    if not script.exists():
        _log(f"FATAL missing script: {script}")
        return 1
    _log(f"batch submit START: {script}")
    proc = subprocess.run(
        ["cmd.exe", "/c", str(script)],
        cwd=str(ROOT),
    )
    _log(f"batch submit END exit={proc.returncode}")
    return proc.returncode


def _enable_regular_batch() -> None:
    proc = subprocess.run(
        ["schtasks", "/Change", "/TN", "Layer2_Batch_Submit", "/Enable"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    _log(f"regular Layer2_Batch_Submit enable exit={proc.returncode}")
    if proc.stdout.strip():
        _log(proc.stdout.strip())
    if proc.stderr.strip():
        _log(proc.stderr.strip())


def main() -> int:
    _log("========================================")
    _log("batch_submit_after_layer2 START")
    try:
        if not _wait_for_layer2():
            return 1
        return _run_batch_submit()
    finally:
        _enable_regular_batch()
        _log("batch_submit_after_layer2 END")
        _log("========================================")


if __name__ == "__main__":
    sys.exit(main())
