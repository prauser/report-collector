# HANDOFF — STEP 02~07 완료, 아키텍처 재설계 논의 필요

## 현재 상태

**STEP 01~07 전체 완료** (테스트 46 passed, 3 skipped)

### 완료된 것

- **STEP 02**: pykrx 설치(v1.2.4), `storage/stock_mapper.py`, `scripts/init_stock_codes.py` (KRX 불가 시 시드 40종목 fallback)
- **STEP 03**: `collector/backfill.py` 보완 (FloodWaitError, message.date→report_date, stock_code 보완), `collector/listener.py` 보완
- **STEP 04**: `parser/repostory.py` 보완 (이전 목표가, 애널리스트, standalone 의견, 마크다운 볼드 제거, Continuing 필터)
- **STEP 05**: `storage/report_repo.py` upsert 보완 (title_normalized 검증, broker fallback, 필드 길이 truncation)
- **STEP 06**: `storage/pdf_archiver.py` 보완 (get_page_count, download_and_archive), `scripts/retry_pdf.py`
- **STEP 07**: `parser/companyreport.py`, `scripts/sync_channels.py`, 레지스트리 라우팅
- **Telegram 인증 완료**: `report_collector.session` 파일 생성됨
- **실제 백필 테스트**: @repostory123에서 limit=50 → 50건 저장 성공
- **DB 노이즈 정리**: `(Continuing...)` 27건 삭제 완료

### DB 현황 (정리 후)

- reports: ~58건 (실제 리포트)
- stock_codes: 40건 (시드 데이터)
- channels: 4개 동기화 완료

### 주요 설정

- PostgreSQL: localhost:5432, db=report_collector, user=rcuser/ab100463
- Python venv: `C:/Users/prauser/Projects/report-collector/.venv/`
- Telegram 세션: `report_collector.session` (재인증 불필요)
- `config/.env`에 TELEGRAM_API_ID, TELEGRAM_API_HASH 설정됨

### 테스트 파일 목록

```
tests/test_db_setup.py      - DB 스키마 검증 (5 tests)
tests/test_stock_codes.py   - 종목코드 매핑 (5 tests)
tests/test_collector.py     - 백필 mock 테스트 (3 tests)
tests/test_parser.py        - 파서 단위 테스트 (15 tests, 2 skipped)
tests/test_storage.py       - upsert 통합 테스트 (5 tests)
tests/test_pdf_archiver.py  - PDF 아카이빙 (8 tests)
tests/test_channels.py      - 채널 동기화/라우팅 (5 tests, 1 skipped)
```

### 실행 명령

```bash
cd C:/Users/prauser/Projects/report-collector
.venv/Scripts/activate
pytest tests/ -v --tb=short   # 전체 테스트
```

---

## 다음 세션에서 할 일: 파싱 아키텍처 재설계 논의

### 문제 인식

실제 @repostory123 메시지를 백필해보니 **정규식 파서의 한계**가 드러남:

1. **broker 파싱 오류**: 마크다운 링크, 볼드 등이 섞여서 broker에 쓰레기 텍스트 유입
   - 예: `이베스트증권 [<원문 Link>](http://...)` 전체가 broker로 잡힘
   - 수정했지만 새로운 엣지케이스가 계속 나올 것
2. **산업 리포트 제목/broker 경계** 모호: `▶ 제목 - 증권사` 패턴이지만 제목에 `-`가 포함되는 경우
3. **`(Continuing...)` 메시지**: 긴 리포트가 여러 메시지로 분할됨
4. **종목코드 없는 리포트**: stock_mapper로 보완 가능하나 종목명 자체가 파싱 안 되는 경우 있음

### 사용자 제안 및 고민 포인트

> "원문 링크(PDF URL)로만 분석하는 게 나은 것 같다"
> "정규식으로만 파싱하면 엣지케이스가 계속 나온다"
> "LLM을 먼저 돌리면 중복 리포트를 LLM이 봐야 하고, 그게 아니면 중복 파싱 방안이 애매하다"

### 논의할 아키텍처 옵션

**Option A: 2단계 파이프라인 (정규식 경량 파싱 → LLM 정밀 파싱)**
- 1단계: 정규식으로 PDF URL + 최소 메타만 추출 (중복 판별용)
- 2단계: 중복 아닌 건만 LLM으로 정밀 파싱 (broker, 종목, 의견 등)
- 장점: LLM 비용 절감 (중복은 안 봄)
- 단점: 1단계 중복 판별이 부정확하면 누락 발생

**Option B: PDF URL 기반 중복 제거 → LLM 전수 파싱**
- PDF URL을 유니크 키로 사용 (같은 PDF = 같은 리포트)
- 중복 아닌 건만 LLM 파싱
- 장점: 가장 정확한 중복 판별
- 단점: PDF URL 없는 메시지 처리 불가

**Option C: LLM 전수 파싱 + DB 중복 제거**
- 모든 메시지를 LLM으로 파싱 후 DB upsert에서 중복 처리
- 장점: 파싱 정확도 최고
- 단점: LLM 비용 (메시지당 ~$0.01 추정, 월 수천건이면 감당 가능?)

### KRX API 이슈

- pykrx가 KRX API에서 403 반환 (현재 환경에서 접근 불가)
- 시드 데이터 40종목으로 대체 중
- 네트워크 환경 바뀌면 `python -m scripts.init_stock_codes` 재실행으로 전체 종목 로드 가능

---

## 환경 재진입

```bash
cd C:/Users/prauser/Projects/report-collector
.venv/Scripts/activate
```

## 커밋 상태

아직 미커밋. 변경 파일 다수 (STEP 02~07 전체 + 파서 보완 + 테스트).
