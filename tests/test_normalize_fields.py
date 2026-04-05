"""Tests for scripts/normalize_fields.py — dry-run/apply normalization script."""
import asyncio
import argparse
import subprocess
import sys
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def _make_args(apply: bool = False, dry_run: bool = False, batch_size: int = 1000) -> argparse.Namespace:
    return argparse.Namespace(apply=apply, dry_run=dry_run, batch_size=batch_size)


def _import_module():
    import importlib
    import scripts.normalize_fields as m
    return m


# ──────────────────────────────────────────────
# _collect_changes unit tests (pure logic via mock DB)
# ──────────────────────────────────────────────

class TestCollectChanges:

    @pytest.mark.asyncio
    async def test_no_rows_returns_empty(self):
        """Empty DB → no changes."""
        import scripts.normalize_fields as m

        mock_sess = AsyncMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)
        mock_result = MagicMock()
        mock_result.all.return_value = []
        mock_sess.execute = AsyncMock(return_value=mock_result)

        with patch("scripts.normalize_fields.AsyncSessionLocal", return_value=mock_sess):
            changes = await m._collect_changes(batch_size=100)

        assert changes == []

    @pytest.mark.asyncio
    async def test_already_normalized_excluded(self):
        """Rows already normalized → not in changes."""
        import scripts.normalize_fields as m

        # broker="삼성증권", opinion="매수" — no alias, stays same
        mock_sess = AsyncMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)

        call_count = 0
        async def fake_execute(*args, **kwargs):
            nonlocal call_count
            result = MagicMock()
            if call_count == 0:
                result.all.return_value = [(1, "삼성증권", "매수")]
            else:
                result.all.return_value = []
            call_count += 1
            return result

        mock_sess.execute = fake_execute

        with patch("scripts.normalize_fields.AsyncSessionLocal", return_value=mock_sess):
            changes = await m._collect_changes(batch_size=100)

        assert changes == []

    @pytest.mark.asyncio
    async def test_unnormalized_broker_detected(self):
        """broker='미래에셋' → needs normalization."""
        import scripts.normalize_fields as m

        mock_sess = AsyncMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)

        call_count = 0
        async def fake_execute(*args, **kwargs):
            nonlocal call_count
            result = MagicMock()
            if call_count == 0:
                result.all.return_value = [(42, "미래에셋", None)]
            else:
                result.all.return_value = []
            call_count += 1
            return result

        mock_sess.execute = fake_execute

        with patch("scripts.normalize_fields.AsyncSessionLocal", return_value=mock_sess):
            changes = await m._collect_changes(batch_size=100)

        assert len(changes) == 1
        assert changes[0]["id"] == 42
        assert changes[0]["updates"]["broker"] == "미래에셋증권"
        assert "opinion" not in changes[0]["updates"]

    @pytest.mark.asyncio
    async def test_unnormalized_opinion_detected(self):
        """opinion='Buy' → needs normalization."""
        import scripts.normalize_fields as m

        mock_sess = AsyncMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)

        call_count = 0
        async def fake_execute(*args, **kwargs):
            nonlocal call_count
            result = MagicMock()
            if call_count == 0:
                result.all.return_value = [(7, None, "Buy")]
            else:
                result.all.return_value = []
            call_count += 1
            return result

        mock_sess.execute = fake_execute

        with patch("scripts.normalize_fields.AsyncSessionLocal", return_value=mock_sess):
            changes = await m._collect_changes(batch_size=100)

        assert len(changes) == 1
        assert changes[0]["updates"]["opinion"] == "매수"
        assert "broker" not in changes[0]["updates"]

    @pytest.mark.asyncio
    async def test_both_fields_unnormalized(self):
        """Both broker and opinion unnormalized."""
        import scripts.normalize_fields as m

        mock_sess = AsyncMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)

        call_count = 0
        async def fake_execute(*args, **kwargs):
            nonlocal call_count
            result = MagicMock()
            if call_count == 0:
                result.all.return_value = [(99, "한투", "BUY")]
            else:
                result.all.return_value = []
            call_count += 1
            return result

        mock_sess.execute = fake_execute

        with patch("scripts.normalize_fields.AsyncSessionLocal", return_value=mock_sess):
            changes = await m._collect_changes(batch_size=100)

        assert len(changes) == 1
        updates = changes[0]["updates"]
        assert updates["broker"] == "한국투자증권"
        assert updates["opinion"] == "매수"

    @pytest.mark.asyncio
    async def test_null_broker_opinion_skipped(self):
        """NULL broker/opinion → no change needed."""
        import scripts.normalize_fields as m

        mock_sess = AsyncMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)

        call_count = 0
        async def fake_execute(*args, **kwargs):
            nonlocal call_count
            result = MagicMock()
            if call_count == 0:
                result.all.return_value = [(5, None, None)]
            else:
                result.all.return_value = []
            call_count += 1
            return result

        mock_sess.execute = fake_execute

        with patch("scripts.normalize_fields.AsyncSessionLocal", return_value=mock_sess):
            changes = await m._collect_changes(batch_size=100)

        assert changes == []

    @pytest.mark.asyncio
    async def test_pagination_collects_all(self):
        """Two pages of results → both collected."""
        import scripts.normalize_fields as m

        mock_sess = AsyncMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)

        call_count = 0
        async def fake_execute(*args, **kwargs):
            nonlocal call_count
            result = MagicMock()
            if call_count == 0:
                result.all.return_value = [(1, "미래에셋", None)]
            elif call_count == 1:
                result.all.return_value = [(2, "한투", None)]
            else:
                result.all.return_value = []
            call_count += 1
            return result

        mock_sess.execute = fake_execute

        with patch("scripts.normalize_fields.AsyncSessionLocal", return_value=mock_sess):
            changes = await m._collect_changes(batch_size=1)  # batch_size=1 forces pagination

        assert len(changes) == 2
        ids = {c["id"] for c in changes}
        assert ids == {1, 2}


# ──────────────────────────────────────────────
# _apply_changes unit tests
# ──────────────────────────────────────────────

class TestApplyChanges:

    @pytest.mark.asyncio
    async def test_empty_changes_returns_zero(self):
        import scripts.normalize_fields as m
        n = await m._apply_changes([])
        assert n == 0

    @pytest.mark.asyncio
    async def test_updates_each_row(self):
        import scripts.normalize_fields as m

        changes = [
            {"id": 1, "updates": {"broker": "미래에셋증권"}, "before": {"broker": "미래에셋", "opinion": None}},
            {"id": 2, "updates": {"opinion": "매수"}, "before": {"broker": None, "opinion": "Buy"}},
        ]

        executed_updates = []

        mock_sess = AsyncMock()
        mock_sess.__aenter__ = AsyncMock(return_value=mock_sess)
        mock_sess.__aexit__ = AsyncMock(return_value=False)

        async def fake_execute(stmt, *args, **kwargs):
            executed_updates.append(stmt)

        mock_sess.execute = fake_execute
        mock_sess.commit = AsyncMock()

        with patch("scripts.normalize_fields.AsyncSessionLocal", return_value=mock_sess):
            n = await m._apply_changes(changes)

        assert n == 2
        assert mock_sess.commit.call_count == 2


# ──────────────────────────────────────────────
# main() dry-run behavior
# ──────────────────────────────────────────────

class TestMainDryRun:

    @pytest.mark.asyncio
    async def test_dry_run_does_not_call_apply(self, capsys):
        import scripts.normalize_fields as m

        args = _make_args(apply=False)
        changes = [
            {"id": 10, "updates": {"broker": "미래에셋증권"}, "before": {"broker": "미래에셋", "opinion": None}},
        ]

        with patch("scripts.normalize_fields._collect_changes", new_callable=AsyncMock, return_value=changes), \
             patch("scripts.normalize_fields._apply_changes", new_callable=AsyncMock) as mock_apply:
            await m.main(args)

        mock_apply.assert_not_called()

    @pytest.mark.asyncio
    async def test_dry_run_prints_count(self, capsys):
        import scripts.normalize_fields as m

        args = _make_args(apply=False)
        changes = [
            {"id": 1, "updates": {"broker": "미래에셋증권"}, "before": {"broker": "미래에셋", "opinion": None}},
            {"id": 2, "updates": {"opinion": "매수"}, "before": {"broker": None, "opinion": "Buy"}},
        ]

        with patch("scripts.normalize_fields._collect_changes", new_callable=AsyncMock, return_value=changes), \
             patch("scripts.normalize_fields._apply_changes", new_callable=AsyncMock):
            await m.main(args)

        captured = capsys.readouterr()
        assert "2" in captured.out
        assert "DRY-RUN" in captured.out

    @pytest.mark.asyncio
    async def test_no_changes_prints_message(self, capsys):
        import scripts.normalize_fields as m

        args = _make_args(apply=False)

        with patch("scripts.normalize_fields._collect_changes", new_callable=AsyncMock, return_value=[]):
            await m.main(args)

        captured = capsys.readouterr()
        assert "No changes" in captured.out


# ──────────────────────────────────────────────
# main() apply behavior
# ──────────────────────────────────────────────

class TestMainApply:

    @pytest.mark.asyncio
    async def test_apply_calls_apply_changes(self):
        import scripts.normalize_fields as m

        args = _make_args(apply=True)
        changes = [
            {"id": 5, "updates": {"broker": "삼성증권"}, "before": {"broker": "삼성", "opinion": None}},
        ]

        with patch("scripts.normalize_fields._collect_changes", new_callable=AsyncMock, return_value=changes), \
             patch("scripts.normalize_fields._apply_changes", new_callable=AsyncMock, return_value=1) as mock_apply:
            await m.main(args)

        mock_apply.assert_called_once_with(changes)

    @pytest.mark.asyncio
    async def test_apply_prints_done(self, capsys):
        import scripts.normalize_fields as m

        args = _make_args(apply=True)
        changes = [
            {"id": 3, "updates": {"opinion": "중립"}, "before": {"broker": None, "opinion": "HOLD"}},
        ]

        with patch("scripts.normalize_fields._collect_changes", new_callable=AsyncMock, return_value=changes), \
             patch("scripts.normalize_fields._apply_changes", new_callable=AsyncMock, return_value=1):
            await m.main(args)

        captured = capsys.readouterr()
        assert "APPLY" in captured.out
        assert "1" in captured.out


# ──────────────────────────────────────────────
# CLI argument parsing
# ──────────────────────────────────────────────

class TestCli:

    def test_default_is_dry_run(self):
        import scripts.normalize_fields as m
        with patch("sys.argv", ["normalize_fields.py"]):
            args = m.cli()
        assert args.apply is False

    def test_apply_flag(self):
        import scripts.normalize_fields as m
        with patch("sys.argv", ["normalize_fields.py", "--apply"]):
            args = m.cli()
        assert args.apply is True

    def test_dry_run_flag(self):
        import scripts.normalize_fields as m
        with patch("sys.argv", ["normalize_fields.py", "--dry-run"]):
            args = m.cli()
        assert args.dry_run is True

    def test_batch_size_default(self):
        import scripts.normalize_fields as m
        with patch("sys.argv", ["normalize_fields.py"]):
            args = m.cli()
        assert args.batch_size == 1000

    def test_batch_size_custom(self):
        import scripts.normalize_fields as m
        with patch("sys.argv", ["normalize_fields.py", "--batch-size", "500"]):
            args = m.cli()
        assert args.batch_size == 500
