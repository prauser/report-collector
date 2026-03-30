# Report Collector

## Quick Start
```bash
source .venv/bin/activate  # venv 활성화
python run_backfill.py     # 백필 (수집+PDF, forward)
python run_backfill.py --reverse --limit 15000  # 최신부터 역순 수집
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
python run_backfill.py --reverse --limit 15000            # 최신→과거 역순
python run_backfill.py --retry-stage pdf_failed            # PDF 재시도 (retryable만)
python run_backfill.py --retry-stage pdf_failed --all-failures

# PDF 직접 다운로드 (backfill 없이)
python run_download_pending.py --statuses s2a_done --limit 5000
python run_download_pending.py --statuses pdf_failed --limit 1768 --concurrency 2

# 분석
python run_analysis.py --concurrency 8 --batch-size 100    # streaming batch
python run_analysis.py --dry-run                            # 대상만 확인

# 일회성 보정 스크립트
python scripts/backfill_titles.py --apply    # Layer2 meta.title → reports.title
python scripts/backfill_dates.py --apply     # key_data.date → reports.report_date

# DB 마이그레이션
alembic -c db/migrations/alembic.ini upgrade head
```

## Architecture
- **리스너** (`main.py`): 실시간 수집+PDF (분석은 별도)
- **백필** (`run_backfill.py`): 히스토리 수집+PDF (analysis_enabled=False)
  - forward: `last_message_id` 이후 → 오래된 것부터
  - reverse: 최신부터 → `reverse_min_id`로 진행 추적
  - Phase 0.5: DB에서 기존 처리 건 조회 → S2a 재호출 방지
- **PDF 다운로드** (`run_download_pending.py`): DB 직접 쿼리 → Telegram 첨부/t.me/URL fallback
- **분석** (`run_analysis.py`): PDF→key_data(Gemini)→markdown→charts(Gemini)→Layer2(Sonnet Batch)
  - streaming batch: --batch-size건 모이면 Layer2 즉시 제출 (중간 죽어도 손실 최소)
- 수집/분석 분리: 백필은 S2a 분류+PDF만, 분석은 run_analysis.py로 독립 실행

## Pipeline Status Flow
`new` → `s2a_done`/`s2a_skipped`/`s2a_failed` → `pdf_done`/`pdf_failed` → `analysis_pending` → `done`/`analysis_failed`

## 동시 실행 규칙
- analysis + backfill: OK (Telegram vs Gemini/Anthropic)
- analysis + download_pending: OK (DB만 겹침)
- backfill + download_pending: X (Telegram 세션 충돌)
- analysis + backfill_dates: X (둘 다 Gemini API)

## Environment
- DB: PostgreSQL (Railway), pool_size=5, pool_pre_ping=True, pool_recycle=300
- LLM: Anthropic (S2a=Haiku, Layer2=Sonnet Batch), Google Gemini Flash-Lite (key_data, charts)
- PDF 저장: `F:\report-collector\pdfs\` (settings.pdf_base_path)
- Windows에서는 `.venv/Scripts/python.exe`, WSL에서는 `source .venv/bin/activate`
