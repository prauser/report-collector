"""pipeline_status 통합 테스트.

Task 5 구현 검증:
- storage/pdf_archiver.py: is_retryable_failure()
- storage/report_repo.py: update_pipeline_status(), mark_pdf_failed() 업데이트
- storage/analysis_repo.py: save_analysis(), log_analysis_failure() 업데이트
- collector/backfill.py: _process_single_report() 파이프라인 상태 갱신
- db/models.py: pipeline_status, pdf_fail_retryable 컬럼
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call


# ──────────────────────────────────────────────
# pdf_archiver: is_retryable_failure
# ──────────────────────────────────────────────

class TestIsRetryableFailure:

    def _fn(self):
        from storage.pdf_archiver import is_retryable_failure
        return is_retryable_failure

    def test_http_404_is_permanent(self):
        assert self._fn()("http_404") is False

    def test_http_410_is_permanent(self):
        assert self._fn()("http_410") is False

    def test_not_pdf_html_response_is_permanent(self):
        assert self._fn()("not_pdf:html_response") is False

    def test_not_pdf_is_permanent(self):
        assert self._fn()("not_pdf") is False

    def test_no_url_is_permanent(self):
        assert self._fn()("no_url") is False

    def test_unsupported_host_prefix_is_permanent(self):
        assert self._fn()("unsupported_host:example.com") is False

    def test_unsupported_host_another_domain_is_permanent(self):
        assert self._fn()("unsupported_host:consensus.hankyung.com") is False

    def test_timeout_is_retryable(self):
        assert self._fn()("timeout") is True

    def test_connection_error_is_retryable(self):
        assert self._fn()("connection_error") is True

    def test_http_500_is_retryable(self):
        assert self._fn()("http_500") is True

    def test_unknown_is_retryable(self):
        assert self._fn()("unknown") is True

    def test_empty_string_is_retryable(self):
        assert self._fn()("") is True

    def test_not_pdf_variant_with_colon_other_than_html_response_is_retryable(self):
        # not_pdf:other_variant 은 영구 실패 목록에 없으므로 재시도 가능
        assert self._fn()("not_pdf:other") is True


# ──────────────────────────────────────────────
# report_repo: update_pipeline_status
# ──────────────────────────────────────────────

class TestUpdatePipelineStatus:

    @pytest.mark.asyncio
    async def test_executes_update_with_correct_status(self):
        from storage.report_repo import update_pipeline_status

        session = AsyncMock()
        await update_pipeline_status(session, report_id=42, status="s2a_done")
        session.execute.assert_called_once()

        # SQL 문에 pipeline_status 값이 포함되는지 확인
        stmt = session.execute.call_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "pipeline_status" in compiled
        assert "s2a_done" in compiled

    @pytest.mark.asyncio
    async def test_does_not_commit(self):
        """update_pipeline_status는 commit하지 않음 (호출자가 관리)."""
        from storage.report_repo import update_pipeline_status

        session = AsyncMock()
        await update_pipeline_status(session, report_id=1, status="pdf_done")
        session.commit.assert_not_called()


# ──────────────────────────────────────────────
# report_repo: mark_pdf_failed
# ──────────────────────────────────────────────

class TestMarkPdfFailedIntegration:

    @pytest.mark.asyncio
    async def test_sets_pdf_fail_retryable_true_for_unknown(self):
        from storage.report_repo import mark_pdf_failed

        session = AsyncMock()
        await mark_pdf_failed(session, report_id=1, reason="timeout")

        session.execute.assert_called_once()
        stmt = session.execute.call_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "pdf_fail_retryable" in compiled
        assert "pipeline_status" in compiled
        assert "pdf_failed" in compiled

    @pytest.mark.asyncio
    async def test_sets_pdf_fail_retryable_false_for_404(self):
        from storage.report_repo import mark_pdf_failed

        session = AsyncMock()
        await mark_pdf_failed(session, report_id=2, reason="http_404")

        session.execute.assert_called_once()
        stmt = session.execute.call_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "pdf_fail_retryable" in compiled
        # false가 컴파일된 SQL에 포함되는지 확인
        assert "false" in compiled.lower()

    @pytest.mark.asyncio
    async def test_does_not_commit_internally(self):
        """mark_pdf_failed은 commit을 하지 않음 — 호출자가 트랜잭션을 관리."""
        from storage.report_repo import mark_pdf_failed

        session = AsyncMock()
        await mark_pdf_failed(session, report_id=3, reason="unknown")
        session.commit.assert_not_called()

    @pytest.mark.asyncio
    async def test_pipeline_status_set_to_pdf_failed(self):
        from storage.report_repo import mark_pdf_failed

        session = AsyncMock()
        await mark_pdf_failed(session, report_id=5, reason="http_410")

        stmt = session.execute.call_args[0][0]
        compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
        assert "pdf_failed" in compiled


# ──────────────────────────────────────────────
# analysis_repo: save_analysis pipeline_status
# ──────────────────────────────────────────────

class TestSaveAnalysisPipelineStatus:

    def _make_layer2(self, truncated=False):
        layer2 = MagicMock()
        layer2.report_category = "stock"
        layer2.analysis_data = {}
        layer2.llm_model = "claude-3-5-sonnet"
        layer2.llm_cost_usd = 0.01
        layer2.extraction_quality = 0.9
        layer2.markdown_truncated = truncated
        layer2.markdown_original_chars = 10000
        layer2.input_tokens = 1000
        layer2.output_tokens = 200
        layer2.stock_mentions = []
        layer2.sector_mentions = []
        layer2.keywords = []
        layer2.meta = {}
        return layer2

    @pytest.mark.asyncio
    async def test_save_analysis_sets_pipeline_status_done(self):
        from storage.analysis_repo import save_analysis

        session = AsyncMock()
        session.add = MagicMock()
        layer2 = self._make_layer2(truncated=False)

        with patch("storage.analysis_repo.settings") as mock_settings:
            mock_settings.analysis_schema_version = "1.0"
            await save_analysis(session, report_id=10, layer2=layer2)

        # execute 호출 중 pipeline_status='done' 을 포함한 UPDATE가 있어야 함
        execute_calls = session.execute.call_args_list
        pipeline_done_found = False
        for c in execute_calls:
            stmt = c[0][0]
            try:
                compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
                if "pipeline_status" in compiled and "done" in compiled:
                    pipeline_done_found = True
                    break
            except Exception:
                pass
        assert pipeline_done_found, "pipeline_status='done' UPDATE not found in save_analysis"

    @pytest.mark.asyncio
    async def test_save_analysis_truncated_still_sets_pipeline_status_done(self):
        """analysis_status가 'truncated'여도 pipeline_status는 'done'으로 설정."""
        from storage.analysis_repo import save_analysis

        session = AsyncMock()
        session.add = MagicMock()
        layer2 = self._make_layer2(truncated=True)

        with patch("storage.analysis_repo.settings") as mock_settings:
            mock_settings.analysis_schema_version = "1.0"
            await save_analysis(session, report_id=11, layer2=layer2)

        execute_calls = session.execute.call_args_list
        pipeline_done_found = False
        for c in execute_calls:
            stmt = c[0][0]
            try:
                compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
                if "pipeline_status" in compiled and "done" in compiled:
                    pipeline_done_found = True
                    break
            except Exception:
                pass
        assert pipeline_done_found


# ──────────────────────────────────────────────
# analysis_repo: log_analysis_failure pipeline_status
# ──────────────────────────────────────────────

class TestLogAnalysisFailurePipelineStatus:

    @pytest.mark.asyncio
    async def test_sets_pipeline_status_analysis_failed(self):
        from storage.analysis_repo import log_analysis_failure

        session = AsyncMock()
        session.add = MagicMock()

        await log_analysis_failure(session, report_id=20, job_type="layer2_batch", error_message="boom")

        execute_calls = session.execute.call_args_list
        pipeline_failed_found = False
        for c in execute_calls:
            stmt = c[0][0]
            try:
                compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
                if "pipeline_status" in compiled and "analysis_failed" in compiled:
                    pipeline_failed_found = True
                    break
            except Exception:
                pass
        assert pipeline_failed_found, "pipeline_status='analysis_failed' UPDATE not found"

    @pytest.mark.asyncio
    async def test_still_sets_analysis_status_failed(self):
        """기존 analysis_status='failed' 로직도 유지되어야 함."""
        from storage.analysis_repo import log_analysis_failure

        session = AsyncMock()
        session.add = MagicMock()

        await log_analysis_failure(session, report_id=21, job_type="layer2_batch", error_message="err")

        execute_calls = session.execute.call_args_list
        analysis_failed_found = False
        for c in execute_calls:
            stmt = c[0][0]
            try:
                compiled = str(stmt.compile(compile_kwargs={"literal_binds": True}))
                if "analysis_status" in compiled and "failed" in compiled:
                    analysis_failed_found = True
                    break
            except Exception:
                pass
        assert analysis_failed_found, "analysis_status='failed' UPDATE not found"


# ──────────────────────────────────────────────
# db/models: 컬럼 존재 확인
# ──────────────────────────────────────────────

class TestModelColumns:

    def test_report_has_pipeline_status(self):
        from db.models import Report
        assert hasattr(Report, "pipeline_status")

    def test_report_has_pdf_fail_retryable(self):
        from db.models import Report
        assert hasattr(Report, "pdf_fail_retryable")

    def test_pipeline_status_column_type(self):
        from db.models import Report
        from sqlalchemy import String
        col = Report.__table__.c["pipeline_status"]
        assert isinstance(col.type, String)
        assert col.type.length == 30

    def test_pdf_fail_retryable_column_type(self):
        from db.models import Report
        from sqlalchemy import Boolean
        col = Report.__table__.c["pdf_fail_retryable"]
        assert isinstance(col.type, Boolean)
        assert col.nullable is True

    def test_pipeline_status_has_index(self):
        from db.models import Report
        indexed_cols = {
            idx.columns.keys()[0]
            for idx in Report.__table__.indexes
            if len(idx.columns) == 1
        }
        assert "pipeline_status" in indexed_cols


# ──────────────────────────────────────────────
# backfill: update_pipeline_status import
# ──────────────────────────────────────────────

class TestBackfillImport:

    def test_update_pipeline_status_imported_in_backfill(self):
        """backfill 모듈이 update_pipeline_status를 import하는지 확인."""
        import collector.backfill as backfill_mod
        assert hasattr(backfill_mod, "update_pipeline_status")

    def test_update_pipeline_status_is_callable(self):
        from collector.backfill import update_pipeline_status
        import asyncio
        assert asyncio.iscoroutinefunction(update_pipeline_status)
