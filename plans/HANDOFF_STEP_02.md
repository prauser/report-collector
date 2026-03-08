# HANDOFF — STEP 02 시작 전

## 현재 상태

**STEP 01 완료** (커밋: `b1063c9`)

### 완료된 것
- PostgreSQL 17 설치 (`winget`), DB/유저 생성
  - host: localhost:5432
  - db: report_collector
  - user: rcuser / ab100463
- Python venv: `C:/Users/prauser/Projects/report-collector/.venv/`
- requirements.txt 설치 완료
- Alembic 마이그레이션 적용 완료
  - 테이블: reports, stock_codes, channels, alembic_version
  - `uix_report_dedup` NULLS NOT DISTINCT constraint
  - `updated_at` 자동 갱신 트리거
- pytest 5/5 PASS (`tests/test_db_setup.py`)

### 주요 설정 확인사항
- `config/.env` 에 TELEGRAM_API_ID, TELEGRAM_API_HASH 설정됨
- `TELEGRAM_CHANNELS` 는 JSON 배열 형식으로 .env에 저장 (pydantic-settings v2 요구사항)
- `pytest.ini`: `asyncio_default_test_loop_scope = session` 필수

---

## 다음 할 일: STEP 02

`plans/STEP_02_stock_codes.md` 참고.

### 순서
1. `pykrx` 설치
   ```bash
   cd C:/Users/prauser/Projects/report-collector
   .venv/Scripts/pip install pykrx
   ```

2. `storage/stock_mapper.py` 생성 (캐시 기반 종목명→코드 매핑)

3. `scripts/init_stock_codes.py` 실행 (KRX 데이터 로드)
   - 인터넷 필요, 수십 초 소요
   - 결과: stock_codes 테이블에 2500+ 건

4. `tests/test_stock_codes.py` 작성 및 실행

### 주의사항
- pykrx는 영업일 기준 → 주말/공휴일에 실행 시 날짜 fallback 처리 필요
- 종목명 동음이의어 처리: 완전 일치 우선, 복수 결과면 첫 번째 반환
- stock_mapper는 메모리 캐시 사용 (앱 시작 시 1회 로드)

---

## 환경 재진입 명령어

```bash
cd C:/Users/prauser/Projects/report-collector
.venv/Scripts/activate   # Windows
```
