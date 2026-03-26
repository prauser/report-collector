# Report Collector — 통합 로드맵

> 단일 참조 문서. 모든 Phase 계획과 진행 상태는 이 파일에서 관리.
> 마지막 갱신: 2026-03-26

## 인프라 구성

```
NAS (DS216play)   → Telegram 리스너 + PDF 아카이빙 (24/7)
Railway           → FastAPI + PostgreSQL
Vercel            → Next.js 웹
```

---

## 완료

### 수집 인프라 (STEP 01-07)
- [x] Telegram 채널 수집기 (listener + backfill)
- [x] 1단계 파싱: regex 기반 메시지 분류 (3종 파서)
- [x] 2단계 파싱: Claude Haiku LLM 분류/추출 (S2a)
- [x] PDF 다운로드 및 저장
- [x] LLM 비용 추적 (llm_usage 테이블, message_type 필터링)

### Phase 1 — 배포
- [x] Railway에 FastAPI, Vercel에 Next.js 배포
- [x] 채널 관리 웹 UI (추가/활성화/삭제)
- [x] Pending 메시지 검토 UI
- [x] 백필 관리 웹 페이지 (채널별 상태, PDF 커버리지, parse_quality 분포)

### Phase 2 — Layer 2 분석 시스템 (2026-03-15)
- [x] 3-Layer 데이터 아키텍처 설계
- [x] DB 스키마 확장 (6개 신규 테이블 + reports 컬럼 추가)
- [x] S2b + Stage5 → 단일 Layer2 추출(Sonnet 1회)로 통합
- [x] PDF → Markdown 변환 (PyMuPDF4LLM)
- [x] Layer2 구조화 추출 — 투자 논리 체인(Investment Chain) 스키마
- [x] 분석 결과 트랜잭션 저장 + 배치 분석 스크립트
- [x] 웹 통계에 Layer 2 분석 현황 추가
- [x] 20건 실제 리포트 테스트 완료 (19/20 성공)

### 매매 저널 — DB + 백엔드 + FE 골격 (2026-03-22)
- [x] Trade, TradeIndicator, TradePair 모델 + Alembic 마이그레이션
- [x] CSV 파서 (미래에셋 ✅, 삼성 ✅, 키움 스텁)
- [x] trades API 7개 엔드포인트 (308 backend tests)
- [x] 네비게이션 재구성 (리포트 | 매매 | AI Agent | 설정)
- [x] CSV 업로드, 체결 목록, 기본 통계, 복기 페이지 (148 frontend tests)
- [x] AI Agent 챗봇 기본 구조 (api/routers/agent.py, web/app/agent/page.tsx)

### Pipeline 안정화 (2026-03-26)
- [x] Pipeline hang 버그 4건 수정 (chart_digitizer, pdf_archiver, telegram_client, backfill)
- [x] Analysis Phase 1 병렬화 (Queue/Worker 패턴, --concurrency CLI)

---

## 현재 파이프라인 상태 (2026-03-26 기준)

```
pipeline_status       건수
─────────────────────────
s2a_done             12,773    PDF 다운로드 대기 (Telegram 첨부파일)
pdf_failed           12,204    대부분 복구 불가 (서버 차단/서비스 폐쇄)
analysis_pending      8,006    Layer2 분석 대기 (PDF 있음)
new                   2,282    S2a 분류 안 된 초기 데이터
done                  1,116    완료
pdf_done                197    분석 대기 (run_analysis가 자동 포함)
─────────────────────────
합계                 36,578
```

---

## 진행 예정

### Phase A — 데이터 기반 완성

> Layer 2 분석 데이터가 AI Agent(Phase D)와 리포트 시각화(Phase B)의 전제조건.

- [ ] analysis_pending ~8,000건 전체 처리 (`python run_analysis.py --concurrency 8`, Windows 권장)
- [ ] s2a_done 12,773건 Telegram 첨부파일 다운로드 (`python run_backfill.py`)
- [ ] pdf_failed 정리 — retryable 아닌 건 `retryable=False` 일괄 전환
- [ ] S2b (`extract_metadata`) / Stage5 (`pdf_analyzer.py`) 레거시 코드 삭제
- [ ] Railway/Vercel 재배포 확인 (Layer2 + 매매 저널 변경분 반영)
- [ ] NAS 리스너 상시 실행 설정

### Phase B — 리포트 분석 시각화

> masterplan이 설계한 핵심 가치. Layer 2 데이터가 DB에 있지만 웹에서 볼 수 없는 상태를 해소.

**리포트 상세 페이지 Layer2 표시**
- [ ] thesis.summary + sentiment 표시
- [ ] Investment Chain 시각화 (step 타입별 인과관계 흐름)
- [ ] 연관 종목 (report_stock_mentions) / 섹터 / 키워드 표시
- [ ] extraction_quality 표시 + PDF 원문 링크

**종목별 리포트 히스토리**
- [ ] 같은 종목 리포트 시계열 목록
- [ ] 투자의견/목표가 변화 추이 차트
- [ ] report_stock_mentions 기반 크로스 링크 (종목 리포트 ↔ 산업 리포트)

**섹터/산업 분석 뷰**
- [ ] 산업 리포트의 stock_implications 연결 표시
- [ ] 섹터별 리포트 모아보기
- [ ] masterplan 6장의 크로스 리포트 연결 구조(매크로→산업→종목) 시각화

### Phase C — 매매 저널 완성

> expansion-plan Phase 2-3 잔여 작업. price_cache(OHLCV)가 차트와 지표의 전제.

**백엔드**
- [ ] `price_cache` 테이블 Alembic 마이그레이션
- [ ] OHLCV 수집 (`pykrx`) + price_cache 저장
  - 종목별 1년치 배치 조회, 캐시된 날짜 skip
  - CSV 업로드 시 BackgroundTask 자동 수집, 하루 1회 cron 갱신
- [ ] 기술지표 on-demand 계산 (저장 안 함, price_cache 기반)
  - Stochastic Slow: 일/주/월봉 × (5,3,3), (10,6,6), (20,12,12) = 9세트
  - 이평선 5/20/60/120일 — 배열 상태, 괴리율
  - 볼린저밴드 BB(20,2) — 밴드 내 위치, 밴드폭
  - 거래량비율, 캔들패턴
  - snapshot_text 생성 (타임프레임 간 정렬도 요약)
- [ ] 매수-매도 매칭 (FIFO + 평균단가 병행, 부분매도 처리)

**프론트엔드**
- [ ] 캔들차트 페이지 (`trades/chart/[symbol]`)
  - TradingView Lightweight Charts: 캔들 + 볼륨
  - 매수(△) / 매도(▽) 마커
  - 보조지표 토글 (MA/BB on price pane, Stochastic separate pane)
- [ ] 성과 통계 고도화 — 승률, 수익률, 보유기간 (매칭 완료 후)
- [ ] 키움 CSV 파서 (샘플 확보 후)

### Phase D — AI Agent

> Layer 2 데이터가 충분히 쌓인 후 시작. masterplan 6장의 질의 흐름 구현.

- [ ] `agent/context_builder.py`
  - 질문에서 종목명/키워드 추출 → stock_codes 매칭
  - report_stock_mentions + report_sector_mentions + report_keywords 쿼리
  - analysis_data → YAML 변환, 관련도(primary > implication > related) + 최신순 랭킹
  - 15-25건 컨텍스트 윈도우 예산 내 조립
- [ ] `agent/prompt_templates.py`
  - 시스템 프롬프트: 리포트 기반 답변, 일반 지식 활용 시 `[일반 지식]` 명시
  - 한국어 응답 기본
- [ ] `agent/chat_handler.py`
  - Anthropic SDK `.stream()` → SSE 변환
  - llm_usage 기록 (purpose='agent_chat')
  - 일일 예산 체크 (settings.agent_daily_budget_usd)
  - 단순 조회 Haiku / 종합 분석 Sonnet 분기
- [ ] 챗봇 UI 고도화 (기본 구조 완료 상태에서 확장)
  - 마크다운 렌더링 (react-markdown + remark-gfm)
  - 모바일 최적화: 하단 고정 입력창, 자동 스크롤

### Phase E — 크로스 모듈 연동

> 리포트 분석 + 매매 기록을 연결하는 것이 이 시스템의 최종 가치.

- [ ] 매매 차트 페이지 → 해당 종목 리포트 + Layer2 분석 표시
  - report_stock_mentions에서 stock_code 매칭, NULL이면 stock_name ILIKE 폴백
- [ ] 리포트 상세 → 내 매매 내역 표시 (trades 테이블 symbol 매칭)
- [ ] Agent가 리포트 + 매매 이력 동시 참조 가능
- [ ] 대시보드 통합 타임라인 (최근 리포트 + 최근 매매)

### Phase F — 모바일 / PWA

- [ ] `manifest.json` + 서비스워커 (수동 구성, "홈 화면 추가" 지원)
- [ ] 모바일 반응형 — 하단 탭바, 햄버거 메뉴
- [ ] 차트 full-width + 터치 제스처 (핀치줌, 드래그)
- [ ] 입력 요소 최소 44px 터치 타겟

---

## 미결정 사항

| 항목 | 상태 | 메모 |
|------|------|------|
| 계좌별 통계 분리 | 미결정 | 위탁/퇴직연금 성과를 각각 vs 통합 (세금 차이) |
| 해외주식 | 국내만 우선 | 필요 시 yfinance 추가 |
| Expo RN 앱 | 보류 | PWA(Phase F)로 충분한지 확인 후 판단 |
| 키움 CSV 샘플 | 블로커 | HTS에서 내보내기 필요 |

---

## 알려진 이슈

1. **WSL `/mnt/f/` I/O 느림**: PDF 분석 시 건당 70초. Windows 로컬에서 실행 권장
2. **Gemini 503**: 서버 과부하 시 간헐 발생. 10초 backoff 후 1회 재시도 적용됨
3. **Telegram 세션**: WSL에서 복사한 세션이 DC 인증 불일치 가능. Windows 원본 사용 권장
4. **pdf_failed 12,204건**: 대부분 `not_pdf:html_response`(8,083건) + 서버 차단. 복구 불가
5. **pending_messages 재처리 미구현**: "broker_report"로 분류해도 Layer2 파이프라인 자동 트리거 안 됨
6. **stock_codes 테이블**: KRX 전체 적재 상태 미확인 (시드 40종목 + 이후 추가분)

---

## 참조 문서

| 파일 | 내용 | 용도 |
|------|------|------|
| `docs/masterplan.md` | AI Agent 확장 설계 원본 — 3-Layer 아키텍처, Investment Chain 스키마, 크로스 리포트 연결 구조, 포맷 전략 | Phase B/D/E 설계 레퍼런스 |
| `docs/LAYER2_DESIGN.md` | Layer 2 구현 스펙 — DB 스키마, 추출 파이프라인 | 구현 완료, 코드 레퍼런스 |
| `docs/ARCHITECTURE_CURRENT.md` | 현재 아키텍처 + 코드 vs 기획 차이점 15가지 | 주의사항 참조 |
| `docs/webapp-expansion-plan.md` | 매매 저널 + AI Agent 상세 스펙 — DB 테이블, API, 컴포넌트, 기술 스택 | Phase C/D 상세 스펙 |
| `docs/HANDOFF.md` | 최근 세션 작업 내역 + 운영 상태 | 세션 시작 시 참조 |

## 환경변수 체크리스트

### Railway
| 변수 | 설명 |
|---|---|
| `DATABASE_URL` | PostgreSQL 연결 (자동 링크) |
| `ANTHROPIC_API_KEY` | Claude API 키 |
| `ALLOWED_ORIGINS` | Vercel 도메인 |

### Vercel
| 변수 | 설명 |
|---|---|
| `NEXT_PUBLIC_API_URL` | Railway API URL |

### NAS
| 변수 | 설명 |
|---|---|
| `DATABASE_URL` | Railway PostgreSQL 외부 연결 URL |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | Telegram 앱 키 |
| `ANTHROPIC_API_KEY` | Claude API 키 |
