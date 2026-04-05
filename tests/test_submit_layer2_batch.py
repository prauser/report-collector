"""Tests for submit_layer2_batch() in parser/layer2_extractor.py.

Verifies:
- submit_layer2_batch submits to Anthropic but does NOT poll
- _save_pending_batch is called after successful submission
- layer2_batch_submitted is logged
- The batch_id string is returned
- Retry logic on RateLimitError / APIConnectionError
- Empty requests raises ValueError
- Submission failure (after retries) re-raises
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from anthropic import RateLimitError, APIConnectionError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_batch(batch_id: str = "msgbatch_test_001"):
    batch = MagicMock()
    batch.id = batch_id
    return batch


def _make_requests(n: int = 3):
    """Build fake batch Request dicts (the actual type is Request from anthropic)."""
    return [{"custom_id": f"report-{i}", "params": {}} for i in range(1, n + 1)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSubmitLayer2Batch:

    @pytest.mark.asyncio
    async def test_returns_batch_id_string(self, tmp_path):
        """submit_layer2_batch returns the batch.id as a string."""
        mock_batch = _make_mock_batch("msgbatch_abc123")
        target = tmp_path / "logs" / "pending_batches.jsonl"

        with patch("parser.layer2_extractor._PENDING_BATCHES_PATH", target), \
             patch("parser.layer2_extractor._get_client") as mock_get_client:
            mock_client = mock_get_client.return_value
            mock_client.messages.batches.create = AsyncMock(return_value=mock_batch)

            from parser.layer2_extractor import submit_layer2_batch
            batch_id = await submit_layer2_batch(_make_requests())

        assert batch_id == "msgbatch_abc123"

    @pytest.mark.asyncio
    async def test_does_not_call_retrieve_or_poll(self, tmp_path):
        """submit_layer2_batch never calls retrieve (no polling)."""
        mock_batch = _make_mock_batch("msgbatch_nopoll")
        target = tmp_path / "logs" / "pending_batches.jsonl"

        with patch("parser.layer2_extractor._PENDING_BATCHES_PATH", target), \
             patch("parser.layer2_extractor._get_client") as mock_get_client:
            mock_client = mock_get_client.return_value
            mock_client.messages.batches.create = AsyncMock(return_value=mock_batch)

            from parser.layer2_extractor import submit_layer2_batch
            await submit_layer2_batch(_make_requests())

        mock_client.messages.batches.retrieve.assert_not_called()

    @pytest.mark.asyncio
    async def test_does_not_call_results(self, tmp_path):
        """submit_layer2_batch never calls batches.results (no result collection)."""
        mock_batch = _make_mock_batch("msgbatch_noresults")
        target = tmp_path / "logs" / "pending_batches.jsonl"

        with patch("parser.layer2_extractor._PENDING_BATCHES_PATH", target), \
             patch("parser.layer2_extractor._get_client") as mock_get_client:
            mock_client = mock_get_client.return_value
            mock_client.messages.batches.create = AsyncMock(return_value=mock_batch)

            from parser.layer2_extractor import submit_layer2_batch
            await submit_layer2_batch(_make_requests())

        mock_client.messages.batches.results.assert_not_called()

    @pytest.mark.asyncio
    async def test_saves_pending_batch_after_submit(self, tmp_path):
        """_save_pending_batch is called with the batch_id and custom_ids."""
        mock_batch = _make_mock_batch("msgbatch_save_test")
        target = tmp_path / "logs" / "pending_batches.jsonl"
        requests = _make_requests(4)

        with patch("parser.layer2_extractor._PENDING_BATCHES_PATH", target), \
             patch("parser.layer2_extractor._get_client") as mock_get_client:
            mock_client = mock_get_client.return_value
            mock_client.messages.batches.create = AsyncMock(return_value=mock_batch)

            from parser.layer2_extractor import submit_layer2_batch
            await submit_layer2_batch(requests)

        import json
        assert target.exists(), "pending_batches.jsonl should be created"
        lines = [json.loads(l) for l in target.read_text().splitlines() if l.strip()]
        assert len(lines) == 1
        entry = lines[0]
        assert entry["batch_id"] == "msgbatch_save_test"
        assert entry["count"] == 4
        assert entry["custom_ids"] == ["report-1", "report-2", "report-3", "report-4"]

    @pytest.mark.asyncio
    async def test_logs_layer2_batch_submitted(self, tmp_path):
        """submit_layer2_batch logs layer2_batch_submitted with batch_id and count."""
        mock_batch = _make_mock_batch("msgbatch_log_test")
        target = tmp_path / "logs" / "pending_batches.jsonl"
        requests = _make_requests(2)

        with patch("parser.layer2_extractor._PENDING_BATCHES_PATH", target), \
             patch("parser.layer2_extractor._get_client") as mock_get_client, \
             patch("parser.layer2_extractor.log") as mock_log:
            mock_client = mock_get_client.return_value
            mock_client.messages.batches.create = AsyncMock(return_value=mock_batch)

            from parser.layer2_extractor import submit_layer2_batch
            await submit_layer2_batch(requests)

        # Check that info was called with layer2_batch_submitted
        info_calls = mock_log.info.call_args_list
        event_names = [c[0][0] for c in info_calls if c[0]]
        assert "layer2_batch_submitted" in event_names, \
            f"Expected layer2_batch_submitted log, got: {event_names}"

    @pytest.mark.asyncio
    async def test_raises_on_empty_requests(self):
        """submit_layer2_batch raises ValueError when requests is empty."""
        from parser.layer2_extractor import submit_layer2_batch
        with pytest.raises(ValueError, match="requests must not be empty"):
            await submit_layer2_batch([])

    @pytest.mark.asyncio
    async def test_retries_on_rate_limit_error(self, tmp_path):
        """submit_layer2_batch retries up to 3 times on RateLimitError."""
        mock_batch = _make_mock_batch("msgbatch_retry")
        target = tmp_path / "logs" / "pending_batches.jsonl"
        call_count = [0]

        # Build a fake RateLimitError — needs request and response kwargs
        fake_rate_error = RateLimitError.__new__(RateLimitError)
        Exception.__init__(fake_rate_error, "rate limited")

        async def mock_create(**kwargs):
            call_count[0] += 1
            if call_count[0] < 3:
                raise fake_rate_error
            return mock_batch

        with patch("parser.layer2_extractor._PENDING_BATCHES_PATH", target), \
             patch("parser.layer2_extractor._get_client") as mock_get_client, \
             patch("parser.layer2_extractor.asyncio.sleep", new_callable=AsyncMock):
            mock_client = mock_get_client.return_value
            mock_client.messages.batches.create = mock_create

            from parser.layer2_extractor import submit_layer2_batch
            batch_id = await submit_layer2_batch(_make_requests())

        assert batch_id == "msgbatch_retry"
        assert call_count[0] == 3  # 2 failures + 1 success

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self, tmp_path):
        """submit_layer2_batch raises after all retries exhausted."""
        target = tmp_path / "logs" / "pending_batches.jsonl"

        fake_conn_error = APIConnectionError.__new__(APIConnectionError)
        Exception.__init__(fake_conn_error, "connection refused")

        async def always_fail(**kwargs):
            raise fake_conn_error

        with patch("parser.layer2_extractor._PENDING_BATCHES_PATH", target), \
             patch("parser.layer2_extractor._get_client") as mock_get_client, \
             patch("parser.layer2_extractor.asyncio.sleep", new_callable=AsyncMock):
            mock_client = mock_get_client.return_value
            mock_client.messages.batches.create = always_fail

            from parser.layer2_extractor import submit_layer2_batch
            with pytest.raises(APIConnectionError):
                await submit_layer2_batch(_make_requests())

    @pytest.mark.asyncio
    async def test_pending_not_saved_on_failure(self, tmp_path):
        """If submission fails, _save_pending_batch is NOT called."""
        target = tmp_path / "logs" / "pending_batches.jsonl"

        fake_conn_error = APIConnectionError.__new__(APIConnectionError)
        Exception.__init__(fake_conn_error, "network error")

        async def always_fail(**kwargs):
            raise fake_conn_error

        with patch("parser.layer2_extractor._PENDING_BATCHES_PATH", target), \
             patch("parser.layer2_extractor._get_client") as mock_get_client, \
             patch("parser.layer2_extractor.asyncio.sleep", new_callable=AsyncMock):
            mock_client = mock_get_client.return_value
            mock_client.messages.batches.create = always_fail

            from parser.layer2_extractor import submit_layer2_batch
            with pytest.raises(APIConnectionError):
                await submit_layer2_batch(_make_requests())

        # The file should not have been created
        assert not target.exists(), "pending_batches.jsonl should NOT be created on failure"
