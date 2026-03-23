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
python run_backfill.py --retry-stage pdf_failed          # PDF 재시도 (retryable만)
python run_backfill.py --retry-stage pdf_failed --all-failures

# DB 마이그레이션
alembic -c db/migrations/alembic.ini upgrade head
```

## Architecture
- **리스너** (`main.py`): 실시간 수집+PDF (분석은 별도)
- **백필** (`run_backfill.py`): 히스토리 수집+PDF (analysis_enabled=False)
- **분석** (`run_analysis.py`): PDF→key_data(Gemini)→markdown→charts(Gemini)→Layer2(Sonnet Batch)
- 수집/분석 분리: 백필은 S2a 분류+PDF만, 분석은 run_analysis.py로 독립 실행

## Pipeline Status Flow
`new` → `s2a_done`/`s2a_skipped`/`s2a_failed` → `pdf_done`/`pdf_failed` → `analysis_pending` → `done`/`analysis_failed`

## Environment
- DB: PostgreSQL (Railway), 연결정보는 config/settings.py
- LLM: Anthropic (S2a=Haiku, Layer2=Sonnet Batch), Google Gemini (key_data, charts)
- PDF 저장: `F:\report-collector\pdfs\` (settings.pdf_base_path)
- Windows에서는 `.venv/Scripts/python.exe`, WSL에서는 `source .venv/bin/activate`
