"""Tests for pending batch persistence helpers in parser/layer2_extractor.py.

Covers:
- _save_pending_batch: creates logs dir, appends a valid JSONL line
- _remove_pending_batch: removes matching entry, keeps others, handles missing file
- Integration: save then remove leaves file empty / correct entries
- Concurrent-safe: multiple saves then selective removes
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _patch_pending_path(tmp_path: Path):
    """Context manager that redirects _PENDING_BATCHES_PATH to a temp file."""
    target = tmp_path / "logs" / "pending_batches.jsonl"
    return patch("parser.layer2_extractor._PENDING_BATCHES_PATH", target)


def _read_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            entries.append(json.loads(line))
    return entries


# ---------------------------------------------------------------------------
# _save_pending_batch
# ---------------------------------------------------------------------------

class TestSavePendingBatch:

    def test_creates_logs_dir(self, tmp_path):
        target = tmp_path / "logs" / "pending_batches.jsonl"
        assert not target.parent.exists()
        with _patch_pending_path(tmp_path):
            from parser.layer2_extractor import _save_pending_batch
            _save_pending_batch("msgbatch_001", ["report-1", "report-2"])
        assert target.parent.exists()

    def test_creates_file_with_jsonl_entry(self, tmp_path):
        with _patch_pending_path(tmp_path):
            from parser.layer2_extractor import _save_pending_batch
            _save_pending_batch("msgbatch_abc", ["report-10", "report-20"])

        target = tmp_path / "logs" / "pending_batches.jsonl"
        entries = _read_entries(target)
        assert len(entries) == 1
        e = entries[0]
        assert e["batch_id"] == "msgbatch_abc"
        assert e["count"] == 2
        assert e["custom_ids"] == ["report-10", "report-20"]

    def test_submitted_at_iso_format(self, tmp_path):
        import re
        with _patch_pending_path(tmp_path):
            from parser.layer2_extractor import _save_pending_batch
            _save_pending_batch("msgbatch_ts", ["report-1"])

        target = tmp_path / "logs" / "pending_batches.jsonl"
        entries = _read_entries(target)
        ts = entries[0]["submitted_at"]
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", ts), \
            f"Unexpected timestamp format: {ts!r}"

    def test_multiple_saves_append(self, tmp_path):
        with _patch_pending_path(tmp_path):
            from parser.layer2_extractor import _save_pending_batch
            _save_pending_batch("msgbatch_001", ["report-1"])
            _save_pending_batch("msgbatch_002", ["report-2", "report-3"])

        target = tmp_path / "logs" / "pending_batches.jsonl"
        entries = _read_entries(target)
        assert len(entries) == 2
        assert entries[0]["batch_id"] == "msgbatch_001"
        assert entries[1]["batch_id"] == "msgbatch_002"

    def test_empty_custom_ids(self, tmp_path):
        with _patch_pending_path(tmp_path):
            from parser.layer2_extractor import _save_pending_batch
            _save_pending_batch("msgbatch_empty", [])

        target = tmp_path / "logs" / "pending_batches.jsonl"
        entries = _read_entries(target)
        assert entries[0]["count"] == 0
        assert entries[0]["custom_ids"] == []


# ---------------------------------------------------------------------------
# _remove_pending_batch
# ---------------------------------------------------------------------------

class TestRemovePendingBatch:

    def test_removes_matching_entry(self, tmp_path):
        with _patch_pending_path(tmp_path):
            from parser.layer2_extractor import _save_pending_batch, _remove_pending_batch
            _save_pending_batch("msgbatch_001", ["report-1"])
            _save_pending_batch("msgbatch_002", ["report-2"])
            _remove_pending_batch("msgbatch_001")

        target = tmp_path / "logs" / "pending_batches.jsonl"
        entries = _read_entries(target)
        assert len(entries) == 1
        assert entries[0]["batch_id"] == "msgbatch_002"

    def test_keeps_other_entries(self, tmp_path):
        with _patch_pending_path(tmp_path):
            from parser.layer2_extractor import _save_pending_batch, _remove_pending_batch
            _save_pending_batch("msgbatch_A", ["report-1"])
            _save_pending_batch("msgbatch_B", ["report-2"])
            _save_pending_batch("msgbatch_C", ["report-3"])
            _remove_pending_batch("msgbatch_B")

        target = tmp_path / "logs" / "pending_batches.jsonl"
        entries = _read_entries(target)
        ids = [e["batch_id"] for e in entries]
        assert "msgbatch_A" in ids
        assert "msgbatch_B" not in ids
        assert "msgbatch_C" in ids

    def test_no_error_if_file_missing(self, tmp_path):
        """_remove_pending_batch should not raise if file does not exist."""
        with _patch_pending_path(tmp_path):
            from parser.layer2_extractor import _remove_pending_batch
            # Should not raise
            _remove_pending_batch("msgbatch_nonexistent")

    def test_no_error_if_batch_id_not_present(self, tmp_path):
        """Removing an ID that was never saved should be a no-op."""
        with _patch_pending_path(tmp_path):
            from parser.layer2_extractor import _save_pending_batch, _remove_pending_batch
            _save_pending_batch("msgbatch_X", ["report-1"])
            _remove_pending_batch("msgbatch_missing")

        target = tmp_path / "logs" / "pending_batches.jsonl"
        entries = _read_entries(target)
        assert len(entries) == 1
        assert entries[0]["batch_id"] == "msgbatch_X"

    def test_remove_only_entry_leaves_empty_file(self, tmp_path):
        with _patch_pending_path(tmp_path):
            from parser.layer2_extractor import _save_pending_batch, _remove_pending_batch
            _save_pending_batch("msgbatch_solo", ["report-5"])
            _remove_pending_batch("msgbatch_solo")

        target = tmp_path / "logs" / "pending_batches.jsonl"
        entries = _read_entries(target)
        assert entries == []

    def test_duplicate_ids_all_removed(self, tmp_path):
        """If the same batch_id was somehow saved twice, both lines are removed."""
        target = tmp_path / "logs" / "pending_batches.jsonl"
        target.parent.mkdir(parents=True, exist_ok=True)
        # Manually write duplicate entries
        line = json.dumps({"batch_id": "msgbatch_dup", "submitted_at": "2026-01-01T00:00:00",
                           "count": 1, "custom_ids": ["report-1"]})
        target.write_text(line + "\n" + line + "\n", encoding="utf-8")

        with patch("parser.layer2_extractor._PENDING_BATCHES_PATH", target):
            from parser.layer2_extractor import _remove_pending_batch
            _remove_pending_batch("msgbatch_dup")

        entries = _read_entries(target)
        assert entries == []


# ---------------------------------------------------------------------------
# Integration: _submit_and_poll_batch calls save/remove
# ---------------------------------------------------------------------------

class TestSubmitAndPollBatchPersistence:

    @pytest.mark.asyncio
    async def test_save_called_after_submission(self, tmp_path):
        """_save_pending_batch is called right after batch submission."""
        target = tmp_path / "logs" / "pending_batches.jsonl"

        mock_batch = type("Batch", (), {
            "id": "msgbatch_test_123",
            "processing_status": "ended",
            "request_counts": type("RC", (), {"processing": 0, "succeeded": 1, "errored": 0, "expired": 0})(),
        })()

        mock_entry = type("Entry", (), {
            "custom_id": "report-42",
            "result": type("R", (), {
                "type": "succeeded",
                "message": type("M", (), {
                    "content": [],
                    "usage": type("U", (), {
                        "input_tokens": 100, "output_tokens": 50,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0,
                    })(),
                })(),
            })(),
        })()

        async def _fake_results(_id):
            async def _gen():
                yield mock_entry
            return _gen()

        mock_client = type("Client", (), {
            "messages": type("M", (), {
                "batches": type("B", (), {
                    "create": None,
                    "retrieve": None,
                    "results": None,
                })(),
            })(),
        })()

        from unittest.mock import AsyncMock, patch as _patch
        import asyncio

        with _patch("parser.layer2_extractor._PENDING_BATCHES_PATH", target), \
             _patch("parser.layer2_extractor._get_client", return_value=mock_client):
            mock_client.messages.batches.create = AsyncMock(return_value=mock_batch)
            mock_client.messages.batches.retrieve = AsyncMock(return_value=mock_batch)
            mock_client.messages.batches.results = AsyncMock(side_effect=_fake_results)

            from parser.layer2_extractor import _submit_and_poll_batch, build_batch_request
            req = build_batch_request("report-42", "some content")
            results, failed = await _submit_and_poll_batch([req])

        # File should exist (was saved) and then removed (batch completed)
        entries = _read_entries(target)
        # After successful completion, entry should be removed
        assert all(e["batch_id"] != "msgbatch_test_123" for e in entries)

    @pytest.mark.asyncio
    async def test_entry_persists_if_polling_fails(self, tmp_path):
        """If an exception is raised during polling, the pending entry is NOT removed."""
        target = tmp_path / "logs" / "pending_batches.jsonl"

        mock_batch = type("Batch", (), {
            "id": "msgbatch_crash",
            "processing_status": "in_progress",
            "request_counts": type("RC", (), {"processing": 1, "succeeded": 0, "errored": 0, "expired": 0})(),
        })()

        from unittest.mock import AsyncMock, patch as _patch

        with _patch("parser.layer2_extractor._PENDING_BATCHES_PATH", target), \
             _patch("parser.layer2_extractor._get_client") as mock_get_client:
            mock_client = mock_get_client.return_value
            mock_client.messages.batches.create = AsyncMock(return_value=mock_batch)
            # retrieve raises on first poll — simulates process death
            mock_client.messages.batches.retrieve = AsyncMock(side_effect=RuntimeError("network error"))

            from parser.layer2_extractor import _submit_and_poll_batch, build_batch_request
            req = build_batch_request("report-99", "content")
            with pytest.raises(RuntimeError):
                await _submit_and_poll_batch([req])

        # Entry should still be in the file (not removed since completion never ran)
        entries = _read_entries(target)
        assert any(e["batch_id"] == "msgbatch_crash" for e in entries)
