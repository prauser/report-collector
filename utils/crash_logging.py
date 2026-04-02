"""Crash logging utilities for long-running analysis/backfill processes.

Provides:
- asyncio exception handler for fire-and-forget task failures
- sys.excepthook for unhandled main-thread exceptions
- atexit handler to log clean vs unclean exit
- Sentinel file to detect prior crash on next startup

Windows compatible — avoids SIGUSR1/SIGUSR2, only optionally installs SIGTERM.

Usage::

    from utils.crash_logging import setup_crash_logging

    if __name__ == "__main__":
        setup_crash_logging(sentinel_name=".analysis_running", process_name="run_analysis")
        asyncio.run(main(args))
"""
from __future__ import annotations

import atexit
import json
import os
import signal
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog

log = structlog.get_logger(__name__)

# Module-level flag: set to True just before normal exit so atexit can tell
# the difference between a clean shutdown and an abrupt one.
_clean_exit = False
_sentinel_path: Optional[Path] = None


# ---------------------------------------------------------------------------
# Sentinel file helpers
# ---------------------------------------------------------------------------

def _write_sentinel(path: Path, process_name: str) -> None:
    """Create sentinel file with PID and start time."""
    data = {
        "pid": os.getpid(),
        "process_name": process_name,
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        path.write_text(json.dumps(data), encoding="utf-8")
    except OSError as e:
        log.warning("sentinel_write_failed", path=str(path), error=str(e))


def _remove_sentinel(path: Path) -> None:
    """Delete sentinel file (called on clean exit)."""
    try:
        path.unlink(missing_ok=True)
    except OSError as e:
        log.warning("sentinel_remove_failed", path=str(path), error=str(e))


def _check_previous_crash(path: Path) -> None:
    """If sentinel file already exists, a previous run did not exit cleanly."""
    if not path.exists():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        log.warning(
            "previous_run_crashed",
            sentinel=str(path),
            prev_pid=data.get("pid"),
            prev_process=data.get("process_name"),
            prev_started_at=data.get("started_at"),
        )
    except Exception:
        log.warning("previous_run_crashed", sentinel=str(path))


# ---------------------------------------------------------------------------
# atexit handler
# ---------------------------------------------------------------------------

def _atexit_handler() -> None:
    global _clean_exit, _sentinel_path
    if _clean_exit:
        log.info("process_exit", status="clean")
    else:
        log.warning("process_exit", status="unclean_or_exception")
    if _sentinel_path is not None:
        _remove_sentinel(_sentinel_path)


# ---------------------------------------------------------------------------
# sys.excepthook
# ---------------------------------------------------------------------------

def _make_excepthook(process_name: str):
    def _excepthook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            # Let KeyboardInterrupt print normally so Ctrl-C still works.
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log.critical(
            "unhandled_exception",
            process=process_name,
            exc_type=exc_type.__name__,
            traceback=tb_str,
        )
    return _excepthook


# ---------------------------------------------------------------------------
# asyncio exception handler
# ---------------------------------------------------------------------------

def _make_asyncio_exception_handler(process_name: str):
    def _handler(loop, context):
        exc = context.get("exception")
        msg = context.get("message", "no message")
        if exc is not None:
            tb_str = "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            )
            log.error(
                "asyncio_unhandled_exception",
                process=process_name,
                message=msg,
                exc_type=type(exc).__name__,
                traceback=tb_str,
            )
        else:
            log.error(
                "asyncio_unhandled_exception",
                process=process_name,
                message=msg,
            )
    return _handler


# ---------------------------------------------------------------------------
# SIGTERM handler (Windows compatible)
# ---------------------------------------------------------------------------

def _make_sigterm_handler(process_name: str):
    def _handler(signum, frame):
        log.warning("signal_received", process=process_name, signal="SIGTERM")
        # Let the process exit; atexit will fire and log unclean exit
        sys.exit(1)
    return _handler


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_crash_logging(
    sentinel_name: str = ".process_running",
    process_name: str = "process",
    base_dir: Optional[Path] = None,
    install_sigterm: bool = True,
) -> None:
    """Install crash logging infrastructure.

    Call this near the top of ``if __name__ == "__main__"`` before
    ``asyncio.run()``.  The asyncio exception handler must be installed
    separately on the event loop — call :func:`install_asyncio_handler`
    from inside the async entry point.

    Args:
        sentinel_name: File name for the sentinel (e.g. ``.analysis_running``).
        process_name: Human-readable name used in log events.
        base_dir: Directory for the sentinel file. Defaults to the current
                  working directory.
        install_sigterm: Whether to install a SIGTERM handler (default True).
    """
    global _sentinel_path, _clean_exit
    _clean_exit = False

    # Sentinel file location
    dir_ = base_dir if base_dir is not None else Path.cwd()
    _sentinel_path = dir_ / sentinel_name

    # Detect prior crash before overwriting the sentinel
    _check_previous_crash(_sentinel_path)

    # Write new sentinel
    _write_sentinel(_sentinel_path, process_name)

    # Register atexit (fires on normal exit AND on sys.exit())
    atexit.register(_atexit_handler)

    # sys.excepthook for main-thread unhandled exceptions
    sys.excepthook = _make_excepthook(process_name)

    # SIGTERM handler (Windows supports SIGTERM)
    if install_sigterm:
        try:
            signal.signal(signal.SIGTERM, _make_sigterm_handler(process_name))
        except (OSError, ValueError):
            # May fail in certain environments (e.g., non-main thread)
            pass

    log.info(
        "crash_logging_setup",
        process=process_name,
        sentinel=str(_sentinel_path),
        pid=os.getpid(),
    )


def install_asyncio_handler(loop, process_name: str = "process") -> None:
    """Install the asyncio exception handler on *loop*.

    Call this from inside the async entry point after the loop is running::

        async def main(args):
            install_asyncio_handler(asyncio.get_event_loop(), "run_analysis")
            ...
    """
    loop.set_exception_handler(_make_asyncio_exception_handler(process_name))


def mark_clean_exit() -> None:
    """Call this just before the process returns normally from main().

    This lets the atexit handler know the exit was intentional so it logs
    ``clean`` instead of ``unclean_or_exception``.
    """
    global _clean_exit
    _clean_exit = True
