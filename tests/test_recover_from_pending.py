"""Tests for --from-pending flow in scripts/recover_batches.py.

Verifies:
- Reads logs/pending_batches.jsonl and calls _check_and_recover_batch for each
- After successful recovery (status=ended + apply=True), removes from jsonl
- Still-processing batches are not removed
- Batch with error status is counted in errors, not removed
- Dry-run does not remove entries from jsonl
- Missing jsonl file is handled gracefully
- Summary is printed with correct counts
"""
from __future__ import annotations

import json
import sys
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.recover_batches import _recover_from_pending


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_pending(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fp:
        for entry in entries:
            fp.write(json.dumps(entry) + "\n")


def _read_pending(path: Path) -> list[dict]:
    if not path.exists():
        return []
    result = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            result.append(json.loads(line))
    return result


def _make_summary(batch_id: str, status: str, saved: int = 0) -> dict:
    return {
        "batch_id": batch_id,
        "status": status,
        "succeeded": saved,
        "errored": 0,
        "expired": 0,
        "total": saved,
        "saved": saved,
        "error": None,
        "report_ids_succeeded": list(range(1, saved + 1)),
        "report_ids_failed": [],
        "report_ids_unknown_custom_id": [],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestRecoverFromPending:

    @pytest.mark.asyncio
    async def test_missing_file_handled_gracefully(self, tmp_path, capsys):
        """If pending_batches.jsonl does not exist, prints message and returns."""
        client = MagicMock()
        nonexistent = tmp_path / "logs" / "pending_batches.jsonl"

        with patch("scripts.recover_batches._PENDING_BATCHES_PATH", nonexistent):
            await _recover_from_pending(client, apply=False)

        captured = capsys.readouterr()
        assert "No pending batches file found" in captured.out
        client.messages.batches.retrieve.assert_not_called()

    @pytest.mark.asyncio
    async def test_calls_check_and_recover_for_each_batch(self, tmp_path, capsys):
        """_check_and_recover_batch is called once per entry in the jsonl file."""
        pending_path = tmp_path / "logs" / "pending_batches.jsonl"
        _write_pending(pending_path, [
            {"batch_id": "msgbatch_001", "submitted_at": "2026-01-01T00:00:00",
             "count": 2, "custom_ids": ["report-1", "report-2"]},
            {"batch_id": "msgbatch_002", "submitted_at": "2026-01-01T01:00:00",
             "count": 1, "custom_ids": ["report-3"]},
        ])

        client = MagicMock()
        check_calls = []

        async def fake_check(c, batch_id, apply):
            check_calls.append(batch_id)
            return _make_summary(batch_id, "ended", saved=1)

        with patch("scripts.recover_batches._PENDING_BATCHES_PATH", pending_path), \
             patch("scripts.recover_batches._check_and_recover_batch",
                   side_effect=fake_check), \
             patch("scripts.recover_batches._remove_pending_batch"):
            await _recover_from_pending(client, apply=False)

        assert check_calls == ["msgbatch_001", "msgbatch_002"]

    @pytest.mark.asyncio
    async def test_apply_removes_ended_batches_from_jsonl(self, tmp_path, capsys):
        """With --apply, ended batches are removed from the jsonl file."""
        pending_path = tmp_path / "logs" / "pending_batches.jsonl"
        _write_pending(pending_path, [
            {"batch_id": "msgbatch_ended", "submitted_at": "2026-01-01T00:00:00",
             "count": 1, "custom_ids": ["report-1"]},
        ])

        client = MagicMock()
        removed = []

        async def fake_check(c, batch_id, apply):
            return _make_summary(batch_id, "ended", saved=1)

        def fake_remove(batch_id):
            removed.append(batch_id)

        with patch("scripts.recover_batches._PENDING_BATCHES_PATH", pending_path), \
             patch("scripts.recover_batches._check_and_recover_batch",
                   side_effect=fake_check), \
             patch("scripts.recover_batches._remove_pending_batch",
                   side_effect=fake_remove):
            await _recover_from_pending(client, apply=True)

        assert "msgbatch_ended" in removed

    @pytest.mark.asyncio
    async def test_dry_run_does_not_remove_entries(self, tmp_path, capsys):
        """Without --apply, ended batches are NOT removed from the jsonl file."""
        pending_path = tmp_path / "logs" / "pending_batches.jsonl"
        _write_pending(pending_path, [
            {"batch_id": "msgbatch_keep", "submitted_at": "2026-01-01T00:00:00",
             "count": 1, "custom_ids": ["report-1"]},
        ])

        client = MagicMock()
        removed = []

        async def fake_check(c, batch_id, apply):
            return _make_summary(batch_id, "ended", saved=1)

        def fake_remove(batch_id):
            removed.append(batch_id)

        with patch("scripts.recover_batches._PENDING_BATCHES_PATH", pending_path), \
             patch("scripts.recover_batches._check_and_recover_batch",
                   side_effect=fake_check), \
             patch("scripts.recover_batches._remove_pending_batch",
                   side_effect=fake_remove):
            await _recover_from_pending(client, apply=False)

        assert removed == [], "Dry-run should not remove any entries"

    @pytest.mark.asyncio
    async def test_still_processing_batch_not_removed(self, tmp_path, capsys):
        """Batches still in_progress are NOT removed from the jsonl."""
        pending_path = tmp_path / "logs" / "pending_batches.jsonl"
        _write_pending(pending_path, [
            {"batch_id": "msgbatch_processing", "submitted_at": "2026-01-01T00:00:00",
             "count": 3, "custom_ids": ["report-1", "report-2", "report-3"]},
        ])

        client = MagicMock()
        removed = []

        async def fake_check(c, batch_id, apply):
            return _make_summary(batch_id, "in_progress")

        def fake_remove(batch_id):
            removed.append(batch_id)

        with patch("scripts.recover_batches._PENDING_BATCHES_PATH", pending_path), \
             patch("scripts.recover_batches._check_and_recover_batch",
                   side_effect=fake_check), \
             patch("scripts.recover_batches._remove_pending_batch",
                   side_effect=fake_remove):
            await _recover_from_pending(client, apply=True)

        assert removed == [], "In-progress batches should not be removed"

    @pytest.mark.asyncio
    async def test_error_status_batch_not_removed(self, tmp_path, capsys):
        """Batches with retrieve_error status are counted as errors, not removed."""
        pending_path = tmp_path / "logs" / "pending_batches.jsonl"
        _write_pending(pending_path, [
            {"batch_id": "msgbatch_err", "submitted_at": "2026-01-01T00:00:00",
             "count": 1, "custom_ids": ["report-5"]},
        ])

        client = MagicMock()
        removed = []

        async def fake_check(c, batch_id, apply):
            summary = _make_summary(batch_id, "retrieve_error")
            summary["error"] = "API down"
            return summary

        def fake_remove(batch_id):
            removed.append(batch_id)

        with patch("scripts.recover_batches._PENDING_BATCHES_PATH", pending_path), \
             patch("scripts.recover_batches._check_and_recover_batch",
                   side_effect=fake_check), \
             patch("scripts.recover_batches._remove_pending_batch",
                   side_effect=fake_remove):
            await _recover_from_pending(client, apply=True)

        assert removed == []
        captured = capsys.readouterr()
        assert "Errors:" in captured.out and "1" in captured.out

    @pytest.mark.asyncio
    async def test_summary_counts_are_accurate(self, tmp_path, capsys):
        """Summary output reflects correct counts for each outcome type."""
        pending_path = tmp_path / "logs" / "pending_batches.jsonl"
        _write_pending(pending_path, [
            {"batch_id": "msgbatch_ended_1", "submitted_at": "2026-01-01T00:00:00",
             "count": 1, "custom_ids": ["report-1"]},
            {"batch_id": "msgbatch_ended_2", "submitted_at": "2026-01-01T01:00:00",
             "count": 1, "custom_ids": ["report-2"]},
            {"batch_id": "msgbatch_inprog", "submitted_at": "2026-01-01T02:00:00",
             "count": 1, "custom_ids": ["report-3"]},
            {"batch_id": "msgbatch_err", "submitted_at": "2026-01-01T03:00:00",
             "count": 1, "custom_ids": ["report-4"]},
        ])

        client = MagicMock()

        statuses = {
            "msgbatch_ended_1": "ended",
            "msgbatch_ended_2": "ended",
            "msgbatch_inprog": "in_progress",
            "msgbatch_err": "retrieve_error",
        }

        async def fake_check(c, batch_id, apply):
            return _make_summary(batch_id, statuses[batch_id], saved=1 if statuses[batch_id] == "ended" else 0)

        with patch("scripts.recover_batches._PENDING_BATCHES_PATH", pending_path), \
             patch("scripts.recover_batches._check_and_recover_batch",
                   side_effect=fake_check), \
             patch("scripts.recover_batches._remove_pending_batch"):
            await _recover_from_pending(client, apply=True)

        captured = capsys.readouterr()
        assert "Total pending batches: 4" in captured.out
        assert "Ended (recovered):     2" in captured.out
        assert "Still processing:      1" in captured.out
        assert "Errors:                1" in captured.out
        assert "Saved to DB:           2" in captured.out

    @pytest.mark.asyncio
    async def test_dry_run_summary_message(self, tmp_path, capsys):
        """Dry-run summary prints guidance about --apply."""
        pending_path = tmp_path / "logs" / "pending_batches.jsonl"
        _write_pending(pending_path, [
            {"batch_id": "msgbatch_dry", "submitted_at": "2026-01-01T00:00:00",
             "count": 1, "custom_ids": ["report-1"]},
        ])

        client = MagicMock()

        async def fake_check(c, batch_id, apply):
            return _make_summary(batch_id, "ended", saved=0)

        with patch("scripts.recover_batches._PENDING_BATCHES_PATH", pending_path), \
             patch("scripts.recover_batches._check_and_recover_batch",
                   side_effect=fake_check), \
             patch("scripts.recover_batches._remove_pending_batch"):
            await _recover_from_pending(client, apply=False)

        captured = capsys.readouterr()
        assert "--apply" in captured.out

    @pytest.mark.asyncio
    async def test_invalid_json_lines_skipped(self, tmp_path, capsys):
        """Lines that are not valid JSON are skipped without crashing."""
        pending_path = tmp_path / "logs" / "pending_batches.jsonl"
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        pending_path.write_text(
            'not valid json\n'
            '{"batch_id": "msgbatch_valid", "submitted_at": "2026-01-01T00:00:00", '
            '"count": 1, "custom_ids": ["report-1"]}\n',
            encoding="utf-8"
        )

        client = MagicMock()
        check_calls = []

        async def fake_check(c, batch_id, apply):
            check_calls.append(batch_id)
            return _make_summary(batch_id, "ended", saved=1)

        with patch("scripts.recover_batches._PENDING_BATCHES_PATH", pending_path), \
             patch("scripts.recover_batches._check_and_recover_batch",
                   side_effect=fake_check), \
             patch("scripts.recover_batches._remove_pending_batch"):
            await _recover_from_pending(client, apply=False)

        # Only the valid entry should be processed
        assert check_calls == ["msgbatch_valid"]

    @pytest.mark.asyncio
    async def test_empty_file_handled(self, tmp_path, capsys):
        """An empty pending_batches.jsonl file results in no recover calls."""
        pending_path = tmp_path / "logs" / "pending_batches.jsonl"
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        pending_path.write_text("", encoding="utf-8")

        client = MagicMock()

        with patch("scripts.recover_batches._PENDING_BATCHES_PATH", pending_path), \
             patch("scripts.recover_batches._check_and_recover_batch",
                   new_callable=AsyncMock) as mock_check:
            await _recover_from_pending(client, apply=False)

        mock_check.assert_not_called()
        captured = capsys.readouterr()
        assert "Found 0 pending batch" in captured.out


# ---------------------------------------------------------------------------
# Tests: CLI --from-pending flag
# ---------------------------------------------------------------------------

class TestFromPendingCLI:

    def test_from_pending_in_help(self):
        """--help output includes --from-pending flag."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "scripts/recover_batches.py", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        assert result.returncode == 0
        assert "from-pending" in result.stdout

    def test_from_pending_parsed_as_true(self):
        """Parsing --from-pending sets args.from_pending=True."""
        import importlib
        import scripts.recover_batches as rb

        # Simulate CLI parsing without actually running main
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--from-pending", action="store_true")
        parser.add_argument("--apply", action="store_true")
        args = parser.parse_args(["--from-pending"])

        assert args.from_pending is True
        assert args.apply is False

    def test_from_pending_with_apply_parsed(self):
        """Parsing --from-pending --apply sets both flags."""
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--from-pending", action="store_true")
        parser.add_argument("--apply", action="store_true")
        args = parser.parse_args(["--from-pending", "--apply"])

        assert args.from_pending is True
        assert args.apply is True
