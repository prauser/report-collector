"""Tests for utils/crash_logging.py

Verifies:
1. Sentinel file is created on setup with PID and start time
2. Sentinel file is removed on clean exit (via atexit)
3. Existing sentinel file at startup triggers a "previous crash" warning log
4. mark_clean_exit() sets the internal flag; atexit logs "clean"
5. sys.excepthook is replaced and logs critical on unhandled exception
6. asyncio exception handler logs error for uncaught task exceptions
7. SIGTERM handler logs warning and exits
8. KeyboardInterrupt passes through to original excepthook
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import traceback
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_module():
    """Re-import crash_logging with a clean module state."""
    import importlib
    import utils.crash_logging as mod
    # Reset module globals so tests are isolated
    mod._clean_exit = False
    mod._sentinel_path = None
    return mod


# ---------------------------------------------------------------------------
# Sentinel file: creation
# ---------------------------------------------------------------------------

class TestSentinelCreation:

    def test_sentinel_file_created_with_pid_and_time(self, tmp_path):
        mod = _fresh_module()
        mod.setup_crash_logging(
            sentinel_name=".test_running",
            process_name="test_proc",
            base_dir=tmp_path,
            install_sigterm=False,
        )
        sentinel = tmp_path / ".test_running"
        assert sentinel.exists(), "Sentinel file should be created on setup"
        data = json.loads(sentinel.read_text())
        assert data["pid"] == os.getpid()
        assert data["process_name"] == "test_proc"
        assert "started_at" in data

    def test_sentinel_path_stored_in_module(self, tmp_path):
        mod = _fresh_module()
        mod.setup_crash_logging(
            sentinel_name=".test_running",
            process_name="test_proc",
            base_dir=tmp_path,
            install_sigterm=False,
        )
        assert mod._sentinel_path == tmp_path / ".test_running"


# ---------------------------------------------------------------------------
# Sentinel file: removal on clean exit
# ---------------------------------------------------------------------------

class TestSentinelRemoval:

    def test_sentinel_removed_after_mark_clean_and_atexit(self, tmp_path):
        mod = _fresh_module()
        mod.setup_crash_logging(
            sentinel_name=".test_running",
            process_name="test_proc",
            base_dir=tmp_path,
            install_sigterm=False,
        )
        sentinel = tmp_path / ".test_running"
        assert sentinel.exists()

        mod.mark_clean_exit()
        # Simulate atexit firing
        mod._atexit_handler()

        assert not sentinel.exists(), "Sentinel should be removed on clean exit"

    def test_sentinel_removed_even_on_unclean_atexit(self, tmp_path):
        """Even on unclean exit the sentinel is removed (atexit always fires)."""
        mod = _fresh_module()
        mod.setup_crash_logging(
            sentinel_name=".test_running",
            process_name="test_proc",
            base_dir=tmp_path,
            install_sigterm=False,
        )
        sentinel = tmp_path / ".test_running"
        assert sentinel.exists()

        # Do NOT call mark_clean_exit — simulate crash
        mod._atexit_handler()

        assert not sentinel.exists()


# ---------------------------------------------------------------------------
# Previous crash detection
# ---------------------------------------------------------------------------

class TestPreviousCrashDetection:

    def test_existing_sentinel_triggers_warning(self, tmp_path):
        """If sentinel already exists when setup is called, log a warning."""
        sentinel = tmp_path / ".test_running"
        sentinel.write_text(json.dumps({
            "pid": 99999,
            "process_name": "old_proc",
            "started_at": "2026-01-01T00:00:00+00:00",
        }), encoding="utf-8")

        mod = _fresh_module()
        with patch.object(mod, "log") as mock_log:
            mod.setup_crash_logging(
                sentinel_name=".test_running",
                process_name="new_proc",
                base_dir=tmp_path,
                install_sigterm=False,
            )

        warning_calls = mock_log.warning.call_args_list
        crash_logs = [c for c in warning_calls if "previous_run_crashed" in str(c)]
        assert crash_logs, f"Expected 'previous_run_crashed' warning, got: {warning_calls}"

    def test_no_warning_when_no_prior_sentinel(self, tmp_path):
        """No warning if there is no leftover sentinel."""
        mod = _fresh_module()
        with patch.object(mod, "log") as mock_log:
            mod.setup_crash_logging(
                sentinel_name=".test_running",
                process_name="new_proc",
                base_dir=tmp_path,
                install_sigterm=False,
            )

        warning_calls = mock_log.warning.call_args_list
        crash_logs = [c for c in warning_calls if "previous_run_crashed" in str(c)]
        assert not crash_logs, f"Unexpected crash warning: {warning_calls}"


# ---------------------------------------------------------------------------
# atexit: clean vs unclean
# ---------------------------------------------------------------------------

class TestAtexitHandler:

    def test_atexit_logs_clean_when_flag_set(self, tmp_path):
        mod = _fresh_module()
        mod.setup_crash_logging(
            sentinel_name=".test_running",
            process_name="test_proc",
            base_dir=tmp_path,
            install_sigterm=False,
        )
        mod.mark_clean_exit()

        with patch.object(mod, "log") as mock_log:
            mod._atexit_handler()

        info_calls = mock_log.info.call_args_list
        assert any("clean" in str(c) for c in info_calls), (
            f"Expected 'clean' in info log, got: {info_calls}"
        )

    def test_atexit_logs_unclean_when_flag_not_set(self, tmp_path):
        mod = _fresh_module()
        mod.setup_crash_logging(
            sentinel_name=".test_running",
            process_name="test_proc",
            base_dir=tmp_path,
            install_sigterm=False,
        )
        # Do NOT call mark_clean_exit

        with patch.object(mod, "log") as mock_log:
            mod._atexit_handler()

        warning_calls = mock_log.warning.call_args_list
        assert any("unclean" in str(c) for c in warning_calls), (
            f"Expected 'unclean' in warning log, got: {warning_calls}"
        )


# ---------------------------------------------------------------------------
# sys.excepthook
# ---------------------------------------------------------------------------

class TestExcepthook:

    def test_excepthook_logs_critical_on_exception(self):
        import utils.crash_logging as mod
        hook = mod._make_excepthook("test_proc")

        with patch.object(mod, "log") as mock_log:
            try:
                raise RuntimeError("boom")
            except RuntimeError:
                exc_type, exc_value, exc_tb = sys.exc_info()
            hook(exc_type, exc_value, exc_tb)

        critical_calls = mock_log.critical.call_args_list
        assert critical_calls, "Expected critical log for unhandled exception"
        first = str(critical_calls[0])
        assert "unhandled_exception" in first
        assert "RuntimeError" in first

    def test_excepthook_passes_keyboard_interrupt_through(self):
        """KeyboardInterrupt should delegate to sys.__excepthook__, not be swallowed."""
        import utils.crash_logging as mod
        hook = mod._make_excepthook("test_proc")

        original_called = [False]
        original_hook = sys.__excepthook__

        def fake_original(et, ev, tb):
            original_called[0] = True

        with patch.object(mod, "log") as mock_log:
            with patch.object(sys, "__excepthook__", fake_original):
                try:
                    raise KeyboardInterrupt()
                except KeyboardInterrupt:
                    exc_type, exc_value, exc_tb = sys.exc_info()
                hook(exc_type, exc_value, exc_tb)

        assert original_called[0], "KeyboardInterrupt should call sys.__excepthook__"
        # No critical log for KeyboardInterrupt
        assert not mock_log.critical.called


# ---------------------------------------------------------------------------
# asyncio exception handler
# ---------------------------------------------------------------------------

class TestAsyncioExceptionHandler:

    def test_asyncio_handler_logs_error_with_exception(self):
        import utils.crash_logging as mod
        handler = mod._make_asyncio_exception_handler("test_proc")

        try:
            raise ValueError("async task exploded")
        except ValueError as e:
            exc = e

        loop = MagicMock()
        context = {"message": "Task exception was never retrieved", "exception": exc}

        with patch.object(mod, "log") as mock_log:
            handler(loop, context)

        error_calls = mock_log.error.call_args_list
        assert error_calls, "Expected error log for asyncio exception"
        first = str(error_calls[0])
        assert "asyncio_unhandled_exception" in first
        assert "ValueError" in first

    def test_asyncio_handler_logs_error_without_exception(self):
        import utils.crash_logging as mod
        handler = mod._make_asyncio_exception_handler("test_proc")

        loop = MagicMock()
        context = {"message": "Something went wrong in the loop"}

        with patch.object(mod, "log") as mock_log:
            handler(loop, context)

        error_calls = mock_log.error.call_args_list
        assert error_calls
        assert "asyncio_unhandled_exception" in str(error_calls[0])

    @pytest.mark.asyncio
    async def test_install_asyncio_handler_sets_handler_on_loop(self):
        """install_asyncio_handler sets a custom handler on the given loop."""
        import utils.crash_logging as mod
        loop = asyncio.get_event_loop()

        mod.install_asyncio_handler(loop, "test_proc")
        handler = loop.get_exception_handler()
        assert handler is not None, "Exception handler should be set on loop"

        # Fire a fake exception through the handler
        try:
            raise RuntimeError("fire-and-forget error")
        except RuntimeError as e:
            exc = e

        with patch.object(mod, "log") as mock_log:
            handler(loop, {"message": "test", "exception": exc})

        assert mock_log.error.called


# ---------------------------------------------------------------------------
# SIGTERM handler
# ---------------------------------------------------------------------------

class TestSigtermHandler:

    def test_sigterm_handler_calls_sys_exit(self):
        import utils.crash_logging as mod
        handler = mod._make_sigterm_handler("test_proc")

        with patch.object(mod, "log") as mock_log:
            with pytest.raises(SystemExit):
                handler(signal.SIGTERM, None)

        warning_calls = mock_log.warning.call_args_list
        assert any("signal_received" in str(c) for c in warning_calls)

    def test_sigterm_handler_logs_warning_with_process_name(self):
        import utils.crash_logging as mod
        handler = mod._make_sigterm_handler("my_process")

        with patch.object(mod, "log") as mock_log:
            with pytest.raises(SystemExit):
                handler(signal.SIGTERM, None)

        warning_calls = mock_log.warning.call_args_list
        assert any("my_process" in str(c) for c in warning_calls)


# ---------------------------------------------------------------------------
# mark_clean_exit
# ---------------------------------------------------------------------------

class TestMarkCleanExit:

    def test_mark_clean_exit_sets_flag(self):
        import utils.crash_logging as mod
        mod._clean_exit = False
        mod.mark_clean_exit()
        assert mod._clean_exit is True

    def test_mark_clean_exit_idempotent(self):
        import utils.crash_logging as mod
        mod._clean_exit = False
        mod.mark_clean_exit()
        mod.mark_clean_exit()
        assert mod._clean_exit is True
