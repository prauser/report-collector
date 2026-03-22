# Report Collector → Unified Web App 확장 플랜

> 작성일: 2026-03-19
> 목적: report-collector를 매매 저널 + AI Agent 챗봇이 통합된 웹앱으로 확장하기 위한 구현 플랜
> 상태: 플래닝 완료, Phase별 순차 구현 예정

## Context

현재 report-collector는 텔레그램 증권 리포트 수집 + Layer 2 AI 분석 파이프라인이 동작 중이고, Next.js 프론트엔드에서 리포트 검색/통계/백필을 제공하고 있다. 여기에 두 가지를 추가하여 **하나의 웹앱에서 모든 투자 활동을 관리**하려 한다:

1. **매매 저널** (우선): CSV 업로드 → 체결 기록 + 기술지표 + 차트 + 복기
2. **AI Agent 챗봇** (후순위): 수집된 리포트 기반 종합 분석 질의응답

## 사용자 결정 사항

- **구현 순서**: 매매 저널 먼저 → AI Agent는 이후
- **해외주식**: 국내만 우선 (yfinance 미추가). 해외는 필요 시 나중에
- **Agent 응답 범위**: 리포트 데이터가 1차 소스, 일반 지식 활용 시 반드시 명시 (`[일반 지식]` 태그 등)
- **CSV 샘플**: 아직 미확보 → Phase 1에서 DB/API 골격 먼저, 파서는 샘플 확보 후

---

## 1. 최종 레포 구조

```
report-collector/
├── api/
│   ├── main.py                    # [수정] 신규 라우터 등록
│   ├── routers/
│   │   ├── reports.py             # 기존
│   │   ├── stats.py               # 기존 → [수정] 매매 통계 포함
│   │   ├── pending.py             # 기존
│   │   ├── backfill.py            # 기존
│   │   ├── channels.py            # 기존
│   │   ├── trades.py              # [신규] 매매 CRUD + CSV 업로드
│   │   └── agent.py               # [신규] AI Agent 챗봇 SSE
│   └── schemas.py                 # [수정] Trade/Agent 스키마 추가
│
├── trades/                         # [신규] 매매 저널 모듈
│   ├── csv_parsers/
│   │   ├── common.py              # TradeRow 데이터클래스, 브로커 자동감지, 코드 정규화
│   │   ├── mirae.py               # 미래에셋 CSV 파서
│   │   ├── kiwoom.py              # 키움 CSV 파서
│   │   └── samsung.py             # 삼성 CSV 파서
│   ├── indicators.py              # pykrx OHLCV + pandas_ta 지표 계산
│   ├── pairing.py                 # 매수-매도 FIFO 매칭 → trade_pairs
│   └── trade_repo.py              # DB CRUD (trades, trade_indicators, trade_pairs)
│
├── agent/                          # [신규] AI Agent 모듈
│   ├── context_builder.py         # SQL 검색 → Layer 2 YAML 변환 → 프롬프트 조립
│   ├── prompt_templates.py        # 시스템/유저 프롬프트 템플릿
│   └── chat_handler.py            # Claude 스트리밍 호출 + 응답 생성
│
├── collector/                      # 기존 유지
├── parser/                         # 기존 유지
├── storage/                        # 기존 유지
├── db/
│   ├── models.py                  # [수정] Trade, TradeIndicator, TradePair 모델 추가
│   └── migrations/                # [수정] 신규 마이그레이션 추가
│
├── web/
│   ├── app/
│   │   ├── layout.tsx             # [수정] 통합 네비게이션 (리포트/매매/에이전트)
│   │   ├── page.tsx               # 기존 대시보드
│   │   ├── reports/[id]/          # 기존
│   │   ├── trades/                # [신규]
│   │   │   ├── page.tsx           # 체결 목록 + 인라인 메모 입력
│   │   │   ├── upload/page.tsx    # CSV 드래그앤드롭 업로드
│   │   │   ├── chart/[symbol]/page.tsx  # 캔들차트 + 마커 + 보조지표
│   │   │   ├── stats/page.tsx     # 성과 분석 대시보드
│   │   │   └── review/page.tsx    # 복기 미작성 필터
│   │   ├── agent/                 # [신규]
│   │   │   └── page.tsx           # AI 챗봇 UI
│   │   ├── stats/                 # 기존
│   │   ├── pending/               # 기존
│   │   ├── backfill/              # 기존
│   │   └── settings/              # 기존
│   ├── components/
│   │   ├── trades/                # [신규]
│   │   │   ├── CsvUploader.tsx
│   │   │   ├── TradeTable.tsx
│   │   │   ├── TradeFilters.tsx
│   │   │   ├── CandlestickChart.tsx   # TradingView Lightweight Charts
│   │   │   ├── IndicatorToggles.tsx
│   │   │   └── PerformanceCards.tsx
│   │   ├── agent/                 # [신규]
│   │   │   ├── ChatPanel.tsx
│   │   │   └── ChatMessage.tsx    # 마크다운 렌더링
│   │   └── shared/                # [신규]
│   │       ├── StatCard.tsx       # 기존 stats에서 추출
│   │       └── MarkdownRenderer.tsx
│   └── lib/
│       ├── api.ts                 # [수정] trades.*, agent.* 네임스페이스 추가
│       └── trade-types.ts         # [신규] 매매 관련 TS 타입
│
├── requirements.txt               # [수정] pandas_ta, python-multipart, pyyaml 추가
└── Procfile                       # 기존 유지 (uvicorn 단일 프로세스)
```

---

## 2. 기술 스택 결정

| 영역 | 선택 | 이유 |
|------|------|------|
| **차트** | TradingView Lightweight Charts v5 | Canvas 렌더링(모바일 성능), 캔들+마커+지표 네이티브, ~45KB, 오픈소스 |
| **Agent 스트리밍** | SSE (Server-Sent Events) | FastAPI `StreamingResponse` + Anthropic SDK `.stream()` 자연스럽게 연결. WebSocket 대비 배포 단순 (Railway 호환) |
| **기술지표** | pandas_ta | Stochastic/MA/BB 등 지원. OHLCV 캐시 기반 on-demand 계산 (저장 안 함) |
| **가격 데이터** | pykrx (국내만) | 기존 의존성. OHLCV → `price_cache` 테이블에 캐시. 해외는 추후 yfinance |
| **마크다운 렌더** | react-markdown + remark-gfm | Agent 응답 + Layer 2 분석 표시용 |
| **PK 타입** | Integer (SERIAL) | 기존 컨벤션 유지. UUID는 단일 사용자 시스템에서 불필요한 복잡성 |
| **PWA** | manifest.json 수동 구성 | next-pwa 유지보수 불안정. 심플하게 "홈 화면 추가"만 지원 |

### 추가할 의존성

**Python** (`requirements.txt`):
```
pandas_ta
python-multipart    # FastAPI 파일 업로드
pyyaml              # Agent JSONB→YAML 변환
```

**Frontend** (`web/package.json`):
```
lightweight-charts   # 캔들차트
react-markdown       # 마크다운 렌더링
remark-gfm           # GFM 테이블/체크리스트 지원
```

---

## 3. 신규 DB 테이블 (Alembic 마이그레이션)

### trades
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | SERIAL PK | |
| symbol | VARCHAR(20) FK→stock_codes.code | 종목코드 (리포트 연동 키) |
| name | VARCHAR(100) | 종목명 |
| side | VARCHAR(4) | 'buy' / 'sell' |
| traded_at | TIMESTAMPTZ | 체결일시 |
| price | NUMERIC(12,2) | 체결가 |
| quantity | INTEGER | 수량 |
| amount | NUMERIC(14,2) | 체결금액 |
| broker | VARCHAR(20) | 미래에셋/키움/삼성 |
| account_type | VARCHAR(20) | 위탁/퇴직연금/해외주식 |
| market | VARCHAR(10) | KOSPI/KOSDAQ/NYSE/NASDAQ |
| fees | NUMERIC(10,2) | 수수료+세금 |
| reason | TEXT | 매매이유 |
| review | TEXT | 사후 복기 |
| created_at | TIMESTAMPTZ | |

UNIQUE: `(symbol, traded_at, side, price, quantity, broker)` — 중복 업로드 방지

### trade_indicators (미사용)
> 기존 마이그레이션으로 테이블은 존재하지만 사용하지 않음. 지표는 `price_cache` OHLCV 기반 on-demand 계산.

### price_cache (신규)
| 컬럼 | 타입 | 설명 |
|------|------|------|
| symbol | VARCHAR(20) | 종목코드 |
| date | DATE | 거래일 |
| open | NUMERIC(12,2) | 시가 |
| high | NUMERIC(12,2) | 고가 |
| low | NUMERIC(12,2) | 저가 |
| close | NUMERIC(12,2) | 종가 |
| volume | BIGINT | 거래량 |

PK: `(symbol, date)` — 중복 저장 방지

### trade_pairs
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | SERIAL PK | |
| buy_trade_id | INTEGER FK→trades | |
| sell_trade_id | INTEGER FK→trades | |
| profit_rate | NUMERIC(8,4) | 수익률 |
| holding_days | INTEGER | 보유기간 |

---

## 4. 구현 순서 (Phase별)

### Phase 0: 인프라 정비 ✅ (2026-03-22 완료)
- [x] Alembic 현재 DB 상태와 migration 이력 동기화 확인 (stamp 적용)
- [x] `layout.tsx` 네비게이션 재구성 (리포트 | 매매 | AI Agent | 설정)
  - 모바일: 하단 탭바 4개 + 더보기
  - 데스크톱: 기존 수평 내비 그룹화
- [x] `StatCard` 컴포넌트 `web/components/shared/`로 추출 (재사용)

### Phase 1: 매매 저널 — DB + 백엔드 골격 ✅ (2026-03-22 완료)
- [x] `db/models.py`에 Trade, TradeIndicator, TradePair 모델 추가
- [x] Alembic 마이그레이션 생성 + Railway DB 적용 완료
- [x] `trades/csv_parsers/common.py` — TradeRow 데이터클래스, 인코딩 감지(utf-8-sig/utf-8/cp949), 종목코드 정규화
- [x] `trades/csv_parsers/mirae.py` — 미래에셋 CSV 파서 (완전 구현)
- [x] `trades/csv_parsers/samsung.py` — 삼성증권 CSV 파서 (완전 구현, 퇴직연금)
- [ ] `trades/csv_parsers/kiwoom.py` — 키움 CSV 파서 (스텁, 샘플 미확보)
- [x] `trades/trade_repo.py` — upsert (ON CONFLICT DO NOTHING), 목록 조회, 통계 쿼리
- [x] `api/routers/trades.py` 엔드포인트 7개 완성
- [x] 308 backend tests passing

### Phase 2: OHLCV 캐시 + 기술지표 + 매칭 (2-3일)
- [ ] `price_cache` 테이블 Alembic 마이그레이션
- [ ] `trades/ohlcv.py` — pykrx OHLCV 수집 + price_cache 저장
  - 종목별 배치 조회 (이미 캐시된 날짜는 skip)
  - 종목당 1년치(약 250거래일) 수집
  - CSV 업로드 시 BackgroundTask로 자동 수집
  - 하루 1회 cron으로 보유 종목 갱신
- [ ] `trades/indicators.py` — OHLCV 캐시 기반 on-demand 지표 계산 (저장 안 함)
  - **Stochastic Slow**: 일/주/월봉 × 3세트 (5,3,3), (10,6,6), (20,12,12) = 9개
    - 주봉/월봉은 일봉 리샘플링으로 계산
    - 골든/데드크로스, 방향성, 과열권(80↑/20↓) 판별
  - **이평선**: 5/20/60/120일 — 배열 상태(정/역배열), 현재가 괴리율
  - **볼린저밴드**: BB(20,2) — 밴드 내 위치(0~1), 밴드폭 수축/확장
  - **거래량비율**: 당일/20일평균
  - **캔들패턴**: 장대양봉/음봉, 도지, 꼬리 비율, 갭 여부
- [ ] `trades/indicators.py` — snapshot_text 생성
  - 타임프레임 간 Stochastic 정렬도 요약 (핵심)
  - 이평선 배열 + 지지/저항 근접도
  - BB 위치 + 밴드폭 상태
  - 거래량 시그널
  - 캔들패턴 요약
- [ ] `trades/pairing.py` — 매수-매도 매칭
  - **FIFO**: 개별 거래 복기용 (선입선출 매칭)
  - **평균단가**: 포지션 전체 손익 (물타기 포함)
  - 부분 매도 처리 (100주 매수 → 50주 매도 두 번)
  - 수익률 = (매도금액 - 매수금액 - 수수료) / 매수금액

### Phase 3: 매매 저널 — 프론트엔드 (일부 완료, 2026-03-22)
- [x] `trades/upload/page.tsx` — CSV 드래그앤드롭 업로드 + 프리뷰 테이블
- [x] `trades/page.tsx` — 체결 목록 + 인라인 reason/review 편집
- [ ] `trades/chart/[symbol]/page.tsx` — **핵심 페이지** ← Phase 2 필요
  - Lightweight Charts 캔들차트 + 볼륨
  - 매수(초록 △)/매도(빨강 ▽) 마커
  - 보조지표 토글 (MA, BB on price pane / Stochastic 9세트 separate pane)
  - OHLCV는 price_cache에서 서빙, 지표는 프론트에서 계산
- [x] `trades/stats/page.tsx` — 기본 통계 (거래수/금액/종목빈도). 승률/수익률은 Phase 2 후 추가
- [x] `trades/review/page.tsx` — reason/review NULL 필터 + 완료율 통계
- [x] 148 frontend tests passing (vitest)

### Phase 4: AI Agent 챗봇 (3-4일) ← 매매 저널 완료 후
- [ ] `agent/context_builder.py`
  - 질문에서 종목명/키워드 추출 (stock_codes 테이블 매칭)
  - report_stock_mentions + report_sector_mentions + report_keywords 쿼리
  - report_analysis.analysis_data → YAML 변환
  - 관련도(primary > implication > related) + 최신순 랭킹
  - 15-25건 컨텍스트 윈도우 예산 내 조립
- [ ] `agent/prompt_templates.py`
  - 시스템 프롬프트: "수집된 리포트 데이터 기반으로 답변. 일반 지식 활용 시 `[일반 지식]` 명시"
  - 한국어 응답 기본
- [ ] `agent/chat_handler.py`
  - Anthropic SDK `.stream()` → SSE 변환
  - llm_usage 기록 (purpose='agent_chat')
  - 일일 예산 체크 (settings.agent_daily_budget_usd)
- [ ] `api/routers/agent.py`
  - `POST /api/agent/chat` → StreamingResponse (text/event-stream)
- [ ] `web/app/agent/page.tsx` — 챗봇 UI
  - fetch() + ReadableStream으로 SSE 소비 (POST body 필요하므로 EventSource 미사용)
  - 마크다운 렌더링 (react-markdown)
  - 모바일 최적화: 하단 고정 입력창, 자동 스크롤

### Phase 5: 크로스 모듈 연동 (2일)
- [ ] 매매 차트 페이지에서 해당 종목 관련 리포트 표시
  - report_stock_mentions에서 stock_code 매칭
  - stock_code NULL인 경우 stock_name ILIKE 폴백
  - "이 종목 관련 애널리스트 리포트 N건" 섹션
- [ ] 리포트 상세에서 해당 종목 매매 내역 표시
  - trades 테이블에서 symbol 매칭
- [ ] 대시보드 통합: 최근 리포트 + 최근 매매 통합 타임라인

### Phase 6: 모바일 + PWA (1-2일)
- [ ] `web/public/manifest.json` + 서비스워커
- [ ] 모바일 반응형: 햄버거 메뉴, 하단 탭바
- [ ] 차트 페이지 full-width, 터치 제스처
- [ ] 입력 요소 최소 44px 터치 타겟

---

## 5. 상세화가 필요한 영역

| 영역 | 상태 | 상세 |
|------|------|------|
| **CSV 샘플** | **블로커** | 미래에셋/키움/삼성 HTS에서 CSV 내보내기 필요. 파서 구현의 선결 조건. DB/API 골격은 먼저 구현 가능 |
| ~~Stochastic 파라미터~~ | **결정됨** | Slow (5,3,3), (10,6,6), (20,12,12) × 일/주/월봉. 분봉 제외 |
| ~~매매 매칭 전략~~ | **결정됨** | FIFO(개별 복기) + 평균단가(포지션 전체) 병행 |
| ~~기술지표 저장 방식~~ | **결정됨** | trade_indicators 미사용. OHLCV 캐시(price_cache) + on-demand 계산 |
| **계좌별 통계 분리** | 미결정 | 위탁/퇴직연금 성과를 각각? 통합? (세금 차이) |
| ~~Agent 페르소나~~ | **결정됨** | 리포트 기반 + 일반 지식 활용 시 `[일반 지식]` 명시. 한국어 응답 |
| ~~해외주식~~ | **결정됨** | 국내만 우선. yfinance 미추가 |

---

## 6. 우려사항 및 대응

### Alembic 동기화 문제
현재 일부 스키마 변경이 raw SQL로 적용됨. Phase 0에서 `alembic check`로 현재 상태 확인 후, 불일치 시 catch-up 마이그레이션 생성.

### AI Agent 비용
Sonnet 기준 질의 1건당 $0.10-0.25 (30K-75K 입력 토큰). 하루 10건 = $1-2.50, 월 $30-75. 대응:
- `settings.agent_daily_budget_usd` 하드캡
- Prompt Caching 적용 (동일 종목 반복 질의 시 90% 절감)
- 단순 조회는 Haiku, 종합 분석만 Sonnet

### 모바일 UX
사용자 주 디바이스가 모바일. 현재 프론트엔드에 반응형 처리 미흡. 특히 차트 페이지 멀티 패인(캔들+RSI+MACD)의 세로 레이아웃과 터치 제스처(핀치줌, 드래그)가 핵심.

### pykrx 네트워크 속도
OHLCV를 pykrx에서 가져오는데 종목당 1-3초 소요. 대응:
- `price_cache` 테이블에 OHLCV 캐시 (종목당 1년치, PK: symbol+date)
- CSV 업로드 시 BackgroundTask로 수집, 하루 1회 cron 갱신
- 차트 데이터 API + 지표 계산 모두 캐시에서 서빙 (pykrx는 최초/갱신 시만 호출)

### report stock_code NULL 문제
일부 리포트의 stock_code가 NULL (LLM 추출 누락). 매매-리포트 크로스 연동 시 `stock_name ILIKE` 폴백 매칭 필요.

### Railway 단일 프로세스
현재 uvicorn 1 프로세스. 지표 계산(pandas) + Agent 스트리밍이 동시에 무거울 수 있음. BackgroundTask로 분리하되, 문제 발생 시 worker 프로세스를 Procfile에 추가 검토.

---

## 7. 검증 방법

| Phase | 검증 |
|-------|------|
| Phase 1 | CSV 업로드 → DB 저장 확인. `/api/trades` 목록 조회. 중복 업로드 시 upsert 동작 확인 |
| Phase 2 | OHLCV 캐시 저장 확인. on-demand 지표 계산 + snapshot_text 생성 확인. FIFO/평균단가 매칭 수익률 수동 검산 |
| Phase 3 | 모바일 브라우저에서 차트 렌더링 + 마커 표시. 지표 토글 동작. 인라인 메모 저장 |
| Phase 4 | "삼성전자 전망 알려줘" → 관련 리포트 검색 → YAML 컨텍스트 → 스트리밍 응답 확인 |
| Phase 5 | 차트 페이지에서 동일 종목 리포트 목록 표시. 리포트 상세에서 매매 내역 표시 |
| Phase 6 | 모바일 홈 화면 추가 → PWA로 실행. 하단 탭바 + 햄버거 동작 |

---

## 8. 수정할 핵심 파일 목록

| 파일 | 변경 내용 |
|------|-----------|
| `db/models.py` | Trade, TradeIndicator, TradePair 모델 추가 |
| `api/main.py` | trades, agent 라우터 등록 |
| `api/schemas.py` | Trade*, ChatRequest/Response 스키마 |
| `config/settings.py` | agent_daily_budget_usd, agent_model 설정 추가 |
| `requirements.txt` | pandas_ta, python-multipart, pyyaml |
| `web/app/layout.tsx` | 통합 네비게이션 |
| `web/package.json` | lightweight-charts, react-markdown, remark-gfm |
| `web/lib/api.ts` | trades.*, agent.* 네임스페이스 |
