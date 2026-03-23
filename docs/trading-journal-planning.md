# 매매 복기 자동화 시스템 — 플래닝

> AI 컨텍스트 전달용. 최종 업데이트: 2026-03-19

## 목표

매매 체결 내역을 자동 수집·분석하고, 매매 이유를 기록하며, 복기용 대시보드로 시각화하는 시스템.

## 사용자 환경

- 증권사: 미래에셋(증권플러스), 키움, 삼성 — 개인용 API 없음, CSV 수동 내보내기
- 매매 스타일: 종목별 혼합 (데이~장기)
- 주 사용 디바이스: 모바일

## 기존 인프라 (report collector)

| 구성 | 스택 |
|------|------|
| 백엔드 | FastAPI + Uvicorn |
| DB | PostgreSQL (SQLAlchemy + Alembic) |
| 비동기 | asyncpg, aiohttp, aiofiles |
| 주식 데이터 | pykrx |
| LLM | Anthropic Claude, Google Gemini |
| 로깅 | structlog, tenacity |
| 프론트 배포 | Vercel |
| 백엔드 배포 | Railway |

→ 이 인프라에 매매기록 모듈을 추가 확장한다.

## 아키텍처

```
[증권사 HTS/MTS] → CSV 내보내기 (수동)
        ↓
[PWA 웹앱] → CSV 업로드 (드래그앤드롭)
        ↓
[FastAPI 백엔드] → CSV 파싱 → PostgreSQL 저장
        ↓                ↓
  [pykrx/pandas_ta]    [매매이유 입력 API]
  기술지표 자동 계산      웹에서 체결 건별 메모
        ↓                ↓
      PostgreSQL (trades + indicators)
        ↓
[Next.js + TradingView Lightweight Charts]
  캔들차트 + 매수/매도 마커 + 보조지표 + 복기
```

## 의사결정 로그

| 결정 | 선택 | 이유 |
|------|------|------|
| 데이터 수집 | CSV 수동 내보내기 | 미래에셋/삼성 API 없음, 키움은 Windows 전용 |
| 저장소 | PostgreSQL (기존 인프라) | 빠른 쿼리, 집계, report collector와 동일 DB |
| Notion DB | 사용 안 함 | API 느림(초당 3회 제한), 복잡한 집계 불가, PWA가 UI 대체 |
| 대시보드 | Next.js PWA + TradingView Lightweight Charts | Canvas 렌더링으로 모바일 반응성 우수, 앱스토어 불필요 |
| 배포 | Vercel(프론트) + Railway(백엔드+DB) | 기존 인프라 활용 |
| 매매이유 기록 | PWA 웹에서 체결 건별 입력 | CSV 업로드 후 바로 메모 가능, 실시간성 확보 |
| 가격 데이터 | pykrx (기존), yfinance (해외) | 이미 프로젝트에 포함 |
| 기술지표 | pandas_ta | 새로 추가 (유일한 신규 의존성) |

## DB 스키마 (신규 테이블)

### trades

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | UUID PK | |
| symbol | VARCHAR | 종목코드 |
| name | VARCHAR | 종목명 |
| side | ENUM | buy / sell |
| traded_at | TIMESTAMP | 체결일시 |
| price | DECIMAL | 체결가 |
| quantity | INTEGER | 수량 |
| amount | DECIMAL | 체결금액 |
| broker | VARCHAR | 미래에셋 / 키움 / 삼성 |
| account_type | VARCHAR | 위탁 / 퇴직연금 / 해외주식 |
| market | VARCHAR | KOSPI / KOSDAQ / NYSE / NASDAQ |
| fees | DECIMAL | 수수료+세금 |
| reason | TEXT | 매매이유 (웹에서 입력) |
| review | TEXT | 사후 복기 메모 |
| created_at | TIMESTAMP | 레코드 생성일 |

### trade_indicators

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | UUID PK | |
| trade_id | UUID FK → trades | |
| stoch_k_d | JSONB | 일/주/월 Stochastic %K, %D |
| rsi_14 | DECIMAL | RSI(14) |
| macd | JSONB | MACD, Signal, Histogram |
| ma_position | JSONB | 5/20/60/120일 이평 대비 위치 |
| bb_position | DECIMAL | 볼린저밴드 내 위치 (0~1) |
| volume_ratio | DECIMAL | 당일거래량 / 20일평균 |
| snapshot_text | TEXT | 요약 텍스트 (사람이 읽을 용) |

### trade_pairs (매수-매도 매칭)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | UUID PK | |
| buy_trade_id | UUID FK → trades | |
| sell_trade_id | UUID FK → trades | |
| profit_rate | DECIMAL | 수익률 |
| holding_days | INTEGER | 보유기간 |

## API 엔드포인트 (FastAPI 추가분)

```
POST   /trades/upload          CSV 파일 업로드 → 파싱 → DB 저장 + 지표 자동 계산
GET    /trades                 매매기록 목록 (필터: 종목, 기간, 증권사)
PATCH  /trades/{id}/reason     매매이유 입력/수정
PATCH  /trades/{id}/review     복기 메모 입력/수정
GET    /trades/{id}/indicators 해당 체결의 기술지표 상세
GET    /trades/stats           성과 통계 (승률, 평균수익률 등)
GET    /trades/chart-data      종목별 OHLCV + 매매 마커 데이터
```

## 기술지표 계산

체결 건이 DB에 저장되면 자동으로:
1. pykrx(국내) / yfinance(해외)로 체결일 기준 ±60일 OHLCV 수집
2. pandas_ta로 지표 계산
3. trade_indicators 테이블에 저장

| 지표 | 파라미터 | 용도 |
|------|----------|------|
| Stochastic | %K, %D (일/주/월) | 기존 투자헬퍼 매수 신호 |
| RSI | 14일 | 과매수/과매도 |
| MACD | 12/26/9 | 추세 전환 |
| 이동평균 | 5/20/60/120일 | 추세 위치 |
| 볼린저밴드 | 20일, 2σ | 변동성 위치 |
| 거래량 비율 | 당일/20일평균 | 거래량 이상 |

스토캐스틱 매수 조건: Fast > Slow이고 Slow ≤ 20 (일봉), 주봉/월봉 ≤ 20이면 강한 신호.

## CSV 파서

증권사별 CSV 포맷이 다르므로 파서 분리:
- `parser_mirae.py` — 미래에셋
- `parser_kiwoom.py` — 키움
- `parser_samsung.py` — 삼성
- `parser_common.py` — 공통 모델, 종목코드 표준화, 일시 포맷 통일

파서 개발 시 각 증권사 CSV 샘플 필요.

## 프론트엔드 (Next.js PWA)

기존 Vercel 프론트에 페이지 추가:

| 페이지 | 기능 |
|--------|------|
| /trades/upload | CSV 드래그앤드롭 업로드, 파싱 결과 미리보기 |
| /trades | 체결 목록 + 매매이유/복기 인라인 입력 |
| /trades/chart/:symbol | 캔들차트 + 매수/매도 마커 + 보조지표 토글 |
| /trades/stats | 승률, 수익률, 보유기간, 지표별 성과 분석 |
| /trades/review | 복기 미작성 건 필터, 월간/분기 리포트 |

차트: TradingView Lightweight Charts (오픈소스, Canvas 렌더링)

## 개발 로드맵

| 순서 | 작업 | 예상 | 의존성 |
|------|------|------|--------|
| 1 | DB 스키마 + Alembic 마이그레이션 | 0.5일 | 없음 |
| 2 | CSV 파서 (증권사별) | 2~3일 | CSV 샘플 |
| 3 | 업로드 API + 파싱 → DB 저장 | 1일 | 1, 2 |
| 4 | 기술지표 자동 계산 모듈 | 1~2일 | 1, 3 |
| 5 | 매매이유/복기 CRUD API | 0.5일 | 1 |
| 6 | 성과 통계 API | 1일 | 1 |
| 7 | 프론트: CSV 업로드 페이지 | 1일 | 3 |
| 8 | 프론트: 체결 목록 + 메모 입력 | 1~2일 | 5 |
| 9 | 프론트: 차트 + 마커 + 지표 | 3~5일 | 4, 6 |
| 10 | 프론트: 성과 분석 대시보드 | 2~3일 | 6, 9 |

**총 예상: 약 2~3주**

## 워크플로우

1. 매매 체결 (증권플러스/키움/삼성)
2. 증권플러스 종목메모에 매매이유 간단히 메모 (실시간)
3. 주 1~2회 각 HTS에서 CSV 내보내기
4. PWA 웹에서 CSV 업로드 → 자동 파싱 + 지표 계산 + DB 저장
5. 웹에서 각 체결 건에 매매이유 입력 (종목메모 참고)
6. 같은 웹에서 차트 확인 + 복기 메모 작성

## 비용

| 항목 | 비용 |
|------|------|
| Railway (백엔드 + PostgreSQL) | 기존 인프라 |
| Vercel (프론트) | 기존 인프라 |
| pykrx / yfinance / pandas_ta | 무료 |
| **추가 비용** | **₩0** |

## 향후 확장 (Phase 4)

- 종목분석 DB (PostgreSQL 테이블 추가, report collector 리포트와 연동)
- 밸류에이션 자동 계산 (PER/PBR/S-RIM)
- Claude를 활용한 매매 복기 자동 분석/피드백
- 텔레그램 봇으로 매매이유 실시간 입력 (기존 Telethon 활용)
