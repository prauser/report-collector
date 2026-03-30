# Handoff — 2026-03-31 세션

## 현재 상태

### 프로세스
| 작업 | 상태 | 비고 |
|---|---|---|
| `backfill_dates` dry-run | 돌고 있음 (606/4,384) | Gemini Flash-Lite, ~3시간 예상 |
| reverse backfill | 시작 예정 | `--reverse --limit 15000` |
| analysis | 멈춤 | dates 끝나고 재시작 |
| download_pending | 완료 | s2a_done 0건, pdf_failed retry 완료 |

### DB 현황 (2026-03-31 기준)
| pipeline_status | 건수 |
|---|---|
| pdf_done | 12,002 |
| analysis_pending | 16,840 |
| pdf_failed | 13,927 |
| done | 1,407 |
| new | 2,289 |
| s2a_done | 0 |

## 다음 해야 할 것

### 1. backfill_dates 완료 후
```bash
# dry-run 결과 확인 (보정 대상 건수, 샘플)
# 문제 없으면 apply
python scripts/backfill_dates.py --apply
```

### 2. analysis 재시작
```powershell
$env:PYTHONIOENCODING = "utf-8"
.venv\Scripts\python.exe run_analysis.py --concurrency 8 --batch-size 100 2>&1 | Tee-Object -FilePath analysis_2.log
```
- pdf_done 12,002 + analysis_pending 16,840 = ~28,842건 대상
- streaming batch로 100건마다 Layer2 제출 — 중간 죽어도 손실 최소
- Flash-Lite 모델 사용 중 (비용 ~85% 절감)

### 3. reverse backfill
```powershell
.venv\Scripts\python.exe run_backfill.py --reverse --limit 15000 2>&1 | Tee-Object -FilePath backfill_reverse.log
```
- 최신 메시지부터 역순 수집
- `reverse_min_id`로 진행 추적 (여러 번 나눠 실행 가능)
- analysis와 동시 실행 가능 (Telegram vs Gemini/Anthropic)

### 4. forward backfill (나중에)
```powershell
.venv\Scripts\python.exe run_backfill.py --limit 15000 2>&1 | Tee-Object -FilePath backfill.log
```
- Phase 0.5로 기존 건 빠르게 skip
- reverse와 forward가 가운데서 만나면 미처리 구간 0

### 5. new 건 S2a 분류 (미구현)
- `new` 2,289건은 S2a 분류 안 된 상태
- `raw_text`로 Telegram 재접근 없이 분류 가능
- 별도 스크립트 필요 (미구현)

## 동시 실행 규칙
| 조합 | 가능? | 이유 |
|---|---|---|
| analysis + backfill | O | Telegram vs Gemini/Anthropic |
| analysis + download_pending | O | DB만 겹침, pool_pre_ping으로 안정 |
| backfill + download_pending | X | Telegram 세션 충돌 |
| analysis + backfill_dates | X | 둘 다 Gemini API 사용 |

## 이번 세션 주요 변경사항

### 비용 관련
- `gemini-2.5-flash` → `gemini-2.5-flash-lite` (settings.py)
- 가격 테이블 수정 (models.py): Flash $0.30/$2.50, Flash-Lite $0.10/$0.40
- 품질 메트릭 로깅: `q_chars`, `q_table_rows`, `q_digits` (chart_digitizer.py)

### Pipeline 안정성
- `_REPORT_TIMEOUT`: 300초 → 1800초 (run_analysis.py)
- `pool_pre_ping=True`, `pool_recycle=300` (session.py)
- key_data_extractor: Gemini client 캐싱 + `to_thread` 이벤트루프 블로킹 제거
- markdown_converter: pypdf fallback에 60초 timeout
- backfill: `finally`에서 `last_message_id` 항상 업데이트
- analysis: timeout/error시 `analysis_failed` 마킹
- `pdf_fail_reason` truncate to 50 chars (report_repo.py)

### 구조 변경
- `run_analysis.py`: streaming batch (Phase 1 중 N건마다 Layer2 제출)
- `run_download_pending.py`: s2a_done/pdf_failed PDF 직접 다운로드
- `collector/backfill.py`: Phase 0.5 (기존 건 skip), --reverse 모드
- `channels.reverse_min_id` 컬럼 추가 (alembic migration)
- `key_data.date` → `report_date` 반영 (run_analysis.py, backfill.py)
- `scripts/backfill_dates.py`: 기존 잘못된 날짜 보정
- `scripts/backfill_titles.py`: stream() → async for 수정

### 코드 품질 (simplify 리뷰)
- TME 패턴 중복 제거 → `parser.generic.PATTERN_TME_MSG` import
- `_ReportCheck`/`_Report` 중복 import → `ReportModel` 사용
- `_SKIP_STATUSES` 모듈 레벨 상수
- CSV 작성 `csv.writer` 사용
- results dict → `Counter`
- DB 세션 통합 (2개→1개)

## 미해결 이슈
- `new` 2,289건 S2a 분류 스크립트 미구현
- 이미지 사전 필터링 (N/A 20% 절감) — 유료 전환 후 재테스트 필요
- chart_digitizer 테스트 2건 기존 실패 (TestChartDigitizerGateIntegration)
- untracked 파일 정리: `db_check.py` (credential 하드코딩), `test_*.py` 임시 스크립트, csv 파일
