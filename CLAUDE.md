# Report Collector

## Quick Start
```bash
source .venv/bin/activate  # venv 활성화
python run_backfill.py     # 백필 (수집+PDF)
python run_analysis.py     # 분석 (key_data→markdown→charts→Layer2 Batch)
```

## Run Tests
```bash
pytest tests/ --ignore=tests/test_db_setup.py --ignore=tests/test_storage.py --ignore=tests/test_collector.py -v
# test_db_setup, test_storage, test_collector는 live DB 필요
```

## Key Commands
```bash
# 백필 옵션
python run_backfill.py --channel @sunstudy1004 --limit 5000
python run_backfill.py --reverse --limit 15000          # 최신→과거 역순
python run_backfill.py --retry-stage pdf_failed          # PDF 재시도 (retryable만)

# PDF 직접 다운로드 (backfill 없이, s2a_done/pdf_failed 대상)
python run_download_pending.py --statuses s2a_done --limit 5000

# 분석
python run_analysis.py --concurrency 8 --batch-size 100  # streaming batch

# DB 마이그레이션
alembic -c db/migrations/alembic.ini upgrade head
```

## Architecture
- **리스너** (`main.py`): 실시간 수집+PDF (분석은 별도)
- **백필** (`run_backfill.py`): 히스토리 수집+PDF. forward/reverse 양방향, Phase 0.5로 기존 건 skip
  - `settings.analysis_enabled` 가드: 백필 시 분석 단계(key_data/markdown/charts) 스킵
- **PDF 다운로드** (`run_download_pending.py`): DB 직접 쿼리 → Telegram/t.me/URL fallback
- **분석** (`run_analysis.py`): key_data(Gemini)→markdown→charts(Gemini)→Layer2(Sonnet Batch). streaming batch로 N건마다 Layer2 제출
  - markdown 실패(no_markdown/low_quality) → `analysis_failed`로 전이 + CSV 로깅
  - Layer2 배치 실패 → `analysis_pending` 유지 + 파일 로깅
- 수집/분석 분리: 백필은 S2a+PDF만, 분석은 독립 실행

## Shared Modules
- **`storage/pdf_archiver.py`**: PDF 3단계 fallback 통합함수 `attempt_pdf_download()`, `pdf_filename()`. listener/backfill/download_pending 공용
- **`parser/meta_updater.py`**: `apply_layer2_meta()`, `apply_key_data_meta()`, `trunc()`. 필드 정규화(normalize_broker/opinion) 포함

## Pipeline Status Flow
`new` → `s2a_done`/`s2a_skipped`/`s2a_failed` → `pdf_done`/`pdf_failed` → `analysis_pending` → `done`/`analysis_failed`

## Utility Scripts
```bash
# DB 기존 데이터 broker/opinion 정규화
python scripts/normalize_fields.py              # dry-run (기본)
python scripts/normalize_fields.py --apply      # 실제 적용

# 고아 Anthropic 배치 복구
python scripts/recover_batches.py --recover-all --apply

# 날짜 보정 (3단계: regex → telegram → gemini)
python scripts/fix_dates_regex.py --apply
python scripts/fix_dates_telegram.py --apply
python scripts/backfill_dates.py --apply
```

## Failure Logs
- `logs/markdown_failures.csv`: markdown 변환 실패 (report_id, reason, pdf_path)
- `logs/layer2_batch_failures.log`: Layer2 배치 제출 실패 (batch_num, report_ids, error)

## Concurrency Controls
- backfill: workers=20, telegram_sem=5, http_pdf_sem=25, s2a_sem=15
- download_pending: --concurrency(기본5) + telegram_sem=5
- listener: telegram_sem=5
- analysis: workers=4(기본), gemini_keydata gate, gemini_chart gate, batch_sem=3

## 동시 실행 규칙
- analysis + backfill: OK (Telegram vs Gemini/Anthropic)
- backfill + download_pending: X (Telegram 세션 충돌)
- analysis + backfill_dates: X (둘 다 Gemini API)

## Transaction Design
- `upsert_report()`는 `flush()`만 수행 — `commit()`은 호출자가 일괄 처리
- listener/backfill: 리포트 단위 단일 트랜잭션 (upsert + status + PDF 결과 한번에 commit)
- run_analysis: 단계별 독립 세션 (key_data, markdown, Layer2 각각 별도 commit)

## Environment
- DB: PostgreSQL (Railway), 연결정보는 config/settings.py
- LLM: Anthropic (S2a=Haiku, Layer2=Sonnet Batch), Google Gemini Flash-Lite (key_data, charts)
- PDF 저장: `F:\report-collector\pdfs\` (settings.pdf_base_path)
- Windows에서는 `.venv/Scripts/python.exe`, WSL에서는 `source .venv/bin/activate`
