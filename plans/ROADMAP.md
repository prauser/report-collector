# Report Collector — 통합 로드맵

> 단일 참조 문서. 모든 Phase 계획과 진행 상태는 이 파일에서 관리.
> 마지막 갱신: 2026-04-24 (실 코드 감사 기반 동기화)

## 인프라 구성

```
Windows 머신       → Telegram 리스너 + PDF 아카이빙 + 스케줄 분석 (상시)
Railway            → FastAPI + PostgreSQL
Vercel             → Next.js 웹
```

> 기존 NAS(DS216play) 역할은 Windows 머신이 대체 중.
> 머신 이관 절차는 `docs/windows_setup.md` 참조.

---

## 현재 파이프라인 상태 (2026-04-24 KST)

```
pipeline_status       건수
────────────────────────────
analysis_pending     40,975    Layer2 분석 대기 (PDF 있음)
pdf_done             27,113    분석 대기 (run_analysis가 자동 포함)
done                 24,895    완료
pdf_failed           16,005    대부분 복구 불가 (서버 차단/폐쇄)
new                   2,354    S2a 분류 안 된 초기 데이터
analysis_failed         522    markdown/validation 실패
s2a_done                 48    PDF 다운로드 대기
────────────────────────────
합계                111,912
```

진행률 추정: done / (done + analysis_pending + analysis_failed) ≈ **37%**.
백필 유입과 분석 속도가 균형 잡힌 상태. 분석 스케줄(Batch + Layer2 Claude Code 5h 간격) 유지 시 2-3개월 내 수렴 예상.

---

## 완료 ✅

### 수집 인프라 (STEP 01-07, 2026-02~03)
- [x] Telegram 채널 수집기 (listener + backfill)
- [x] 1단계 파싱: regex 메시지 분류 (3종 파서)
- [x] 2단계 파싱: Claude Haiku LLM 분류/추출 (S2a)
- [x] PDF 3단계 fallback 다운로드 (`storage/pdf_archiver.py`)
- [x] LLM 비용 추적 (`llm_usage` 테이블, message_type 필터)

### Phase 1 — 배포 (2026-03)
- [x] Railway FastAPI + Vercel Next.js 배포
- [x] 채널 관리 / pending 검토 / 백필 관리 웹 UI

### Phase 2 — Layer 2 분석 시스템 (2026-03-15)
- [x] 3-Layer 데이터 아키텍처
- [x] DB 스키마 확장 (6개 신규 테이블 + reports 컬럼)
- [x] PDF→Markdown (PyMuPDF4LLM)
- [x] Layer2 구조화 추출 — Investment Chain 스키마
- [x] S2b/Stage5 레거시 제거 — Layer2 통합 추출로 대체 완료
- [x] 분석 결과 트랜잭션 저장 + 배치 스크립트
- [x] 웹 통계에 Layer 2 분석 현황

### 매매 저널 — DB + 백엔드 (2026-03-22)
- [x] Trade, TradeIndicator, TradePair 모델 + Alembic
- [x] trades API 7개 엔드포인트 (308 backend tests)
- [x] 네비게이션 재구성 (리포트 | 매매 | AI Agent | 설정)

### Pipeline 안정화 (2026-03-26)
- [x] Pipeline hang 버그 4건 수정
- [x] run_analysis.py 병렬화 (Queue/Worker, `--concurrency`)
- [x] streaming batch (N건마다 Layer2 Batch 제출)
- [x] 공유 모듈 리팩터링 (`storage/pdf_archiver.py`, `parser/meta_updater.py`)

### Phase B — 리포트 시각화 대부분 완료 (2026-04 추정)
- [x] 종목 분석 API (`GET /api/stocks`, `/api/stocks/{code}/history`)
- [x] 섹터 분석 API (`GET /api/analysis/sectors`, `/api/analysis/sector/{name}`)
- [x] Recharts 도입 (목표가 라인차트, 섹터 도넛차트, sentiment 바차트)
- [x] `/analysis` 종목|섹터 탭, 종목 검색/목록
- [x] `/analysis/stocks/[code]` 종목 히스토리
- [x] `/analysis/sector/[name]` 섹터 내 종목 비교
- [x] **`Layer2Section.tsx` (395줄)** — thesis, sentiment, Investment Chain (STEP_LABELS 한글화 + direction 색상), reports/[id]/page.tsx에서 사용 중

### Phase C — 매매 저널 거의 완료 (2026-04 추정)
- [x] `PriceCache` 모델 + 마이그레이션 `a8f3c1d2e945`
- [x] `trades/ohlcv.py` (237줄) — pykrx 수집
- [x] `trades/indicators.py` (639줄) — Stochastic 9세트, MA 5/20/60/120, BB(20,2), volume_ratio
- [x] `trades/pairing.py` (277줄) — FIFO + 평균단가 매칭
- [x] `trades/trade_repo.py` (275줄) — DB CRUD
- [x] CSV 파서: 미래에셋(186) / 삼성(181) / 공통(190) 완성
- [x] Trade FE 페이지 4종: `upload`, `chart`, `stats`, `review` 모두 구현
- [x] 캔들차트 (Lightweight Charts, 매수/매도 마커)

### Phase D — AI Agent 핵심 완료 (2026-04 추정)
- [x] `agent/context_builder.py` (256줄)
- [x] `agent/prompt_templates.py` (79줄)
- [x] `agent/chat_handler.py` (431줄) — LLMChatProvider Protocol 추상화, Anthropic AsyncAnthropic, SSE 변환, `llm_usage` 기록
- [x] `agent/tools.py` (447줄) — tool use 패턴
- [x] `api/routers/agent.py` — POST `/agent/chat` SSE, ChatSession 관리
- [x] `web/app/agent/page.tsx` — 챗봇 UI
- [x] `MarkdownRenderer.tsx` — react-markdown 기반 렌더링

### 운영 자동화 (2026-04-23~24)
- [x] Windows Task Scheduler 작업 5종 (리스너 + Batch 제출/수거 + Layer2 Claude Code + Opus 폴백)
- [x] `check_exclusive()` 중복 실행 방지 (sentinel + PID 라이브 체크)
- [x] `claude_layer2.py --effort` 옵션
- [x] 머신 이관 자동화 (`migration_snapshot.bat`, `disable_schedules.bat`)
- [x] `docs/windows_setup.md` / `docs/scheduler_setup.md`

---

## 진행 예정 🔜

### Phase A 잔여 — 데이터 기반 마무리 (소)

- [ ] `pdf_fail_retryable` 일괄 정리 스크립트 (컬럼은 있음, 배치 로직 없음)
- [ ] `analysis_pending` 40,975건 소화 — 스케줄 유지하며 자연 감소
- [ ] `stock_codes` KRX 전체 적재 상태 점검
- [ ] Layer2 dump 성능 실험 — PDF→Markdown 변환 concurrency를 4/6/8로 나눠 측정. 각 설정별 처리량(건/시), peak RSS, PyMuPDF/Windows access violation 여부, `pymupdf4llm_timeout` 발생률을 기록한 뒤 운영값 결정. 현재 관측상 Layer2 LLM은 Codex 기준 약 30초/건이고, backlog 처리 병목은 dump 단계의 markdown 변환 timeout/긴 PDF 처리 쪽에 있음.

### Phase B 잔여 — 리포트 시각화 마감 (중)

- [ ] **stock_code 정규화** — `report_stock_mentions` pseudo-code 정리, `reports.stock_code` NULL 재추출, KRX 마스터 최신화 → **Phase D 고도화와 Phase E의 전제**
- [ ] `report_stock_mentions` 크로스링크 UI (매크로→산업→종목 네비게이션)
- [ ] Layer2Section에 `extraction_quality` 표시 + PDF 원문 링크 추가
- [ ] 섹터 내 산업 리포트의 `stock_implications` 연결 표시

### Phase C 잔여 — 매매 저널 폴리싱 (소)

- [ ] **키움 CSV 파서 실구현** — 현재 11줄 스텁 (HTS 샘플 확보 블로커)
- [ ] 성과 통계 고도화 검토 — 승률/수익률/보유기간이 이미 반영됐는지 확인

### Phase D 고도화 — Agent 세부 기능 (중)

- [ ] **일일 예산 체크** — `settings.agent_daily_budget_usd` + 초과 시 차단
- [ ] **Haiku/Sonnet 분기** — 단순 조회는 Haiku, 종합 분석은 Sonnet
- [ ] 챗봇 UI 모바일 최적화 — 하단 고정 입력창, 자동 스크롤
- [ ] 프롬프트 튜닝 — `[일반 지식]` 태그 일관성 점검

### Phase E — 크로스 모듈 연동 (대, **최대 가치**)

> 이 시스템의 최종 가치. 리포트 분석과 매매 기록의 연결.

- [ ] 매매 차트 → 해당 종목의 리포트 + Layer2 분석 표시
  - `report_stock_mentions.stock_code` 매칭 (NULL이면 `stock_name` ILIKE 폴백)
- [ ] 리포트 상세 → 내 매매 내역 표시 (`trades` 테이블 symbol 매칭)
- [ ] Agent가 리포트 + 매매 이력 동시 참조 — `agent/tools.py`에 매매 조회 도구 추가
- [ ] 대시보드 통합 타임라인 (최근 리포트 + 최근 매매)

### Phase F — 모바일 / PWA (중)

- [ ] `web/public/manifest.json` + 서비스워커 (수동 구성)
- [ ] 모바일 반응형 — 하단 탭바, 햄버거 메뉴
- [ ] 차트 full-width + 터치 제스처 (핀치줌, 드래그)
- [ ] 44px 터치 타겟 준수

---

## 권장 진행 순서

```
Phase A 잔여 (소)  ──┐
Phase B: stock_code 정규화 (중) ──→ Phase E 전제
                                       │
Phase D 고도화 (중)                    │
         │                             ▼
         └────────────────→ Phase E: 크로스 연동 (대) ──→ Phase F PWA
                                   ▲
Phase C 키움 파서 (샘플 확보 시) ──┘
```

**1순위 — Phase E 크로스 연동**: 남은 작업 중 시스템 가치가 가장 큼.
**전제 조건 — stock_code 정규화 (Phase B 잔여)**: 크로스 연동의 조인 키.

### 세션 단위 플랜

작업을 Phase별로 뭉치되, 의존관계 기준으로 세션 경계를 분리.
각 세션은 독립적으로 PR 가능 크기를 유지 — 대규모 작업은 분할.

| 세션 | 내용 | 목표 산출물 | 의존 |
|---|---|---|---|
| **S1** | Phase B 잔여 — 리포트 시각화 마감 | `Layer2Section`에 extraction_quality + PDF 원문 링크, `report_stock_mentions` 크로스링크 UI, 섹터 산업 리포트 `stock_implications` 표시 | — |
| **S2** | Phase A+B 공통 — **stock_code 정규화** | 1회성 배치 스크립트: `report_stock_mentions.stock_code` pseudo 정리, `reports.stock_code` NULL 재추출, KRX 마스터 최신화. `pdf_fail_retryable` 일괄 정리도 함께 | — |
| **S3** | Phase D 고도화 — Agent 운영성 | 일일 예산 체크 (`settings.agent_daily_budget_usd`), Haiku/Sonnet 분기 라우팅, 모바일 입력창 고정 + 자동 스크롤 | — |
| **S4** | Phase E-1 — 매매 → 리포트 방향 | `trades/chart/[symbol]` 페이지에 해당 종목 리포트 + Layer2 요약 사이드바, stock_code 매칭(+이름 ILIKE 폴백) | **S2 선행** |
| **S5** | Phase E-2 — 리포트 → 매매 방향 | `reports/[id]` 페이지에 내 매매 내역 섹션, trades.symbol 매칭 | **S2 선행** |
| **S6** | Phase E-3 — Agent가 매매 참조 | `agent/tools.py`에 `search_my_trades`, `get_trade_performance` 도구 추가, 프롬프트 템플릿 보강 | **S3, S4, S5** |
| **S7** | Phase E-4 — 통합 타임라인 | 대시보드에 최근 리포트 + 최근 매매 병합 뷰 | **S4, S5** |
| **S8** | Phase C 잔여 — 키움 CSV 파서 | 실구현 (현재 11줄 스텁) | 샘플 확보 시 |
| **S9** | Phase F — PWA | `manifest.json`, 서비스워커, 하단 탭바, 44px 터치 타겟, 차트 터치 제스처 | S4-S7 후 |

> **메모리 `feedback_session_style.md`**: 대규모 작업은 Phase별 별도 세션으로 분할하는 것이 사용자 선호. 위 세션 경계는 이 선호에 맞춰 설정.

**병렬성 주의**: S1-S3는 서로 독립이라 순서 자유. S4-S5는 S2(stock_code 정규화) 선행 필수.

---

## 미결정 사항

| 항목 | 상태 | 메모 |
|------|------|------|
| 계좌별 통계 분리 | 미결정 | 위탁/퇴직연금 각각 vs 통합 (세금 차이) |
| 해외주식 | 국내만 우선 | 필요 시 yfinance 추가 |
| Expo RN 앱 | 보류 | PWA(Phase F)로 충분한지 확인 후 판단 |
| 키움 CSV 샘플 | 블로커 | HTS 내보내기 필요 |

---

## 알려진 이슈

1. **Gemini 503**: 서버 과부하 시 간헐 발생. 10초 backoff + 1회 재시도 적용됨
2. **Telegram 세션**: 두 머신 동시 사용 시 강제 로그아웃. 이관 시 한쪽 종료 필요 (`docs/windows_setup.md` §6)
3. **pdf_failed 16,005건**: 대부분 `not_pdf:html_response` + 서버 차단. 복구 불가
4. **pending_messages 재처리 미구현**: "broker_report"로 재분류해도 Layer2 파이프라인 자동 트리거 안 됨
5. **`claude_not_found` 연발**: Claude Code CLI PATH 일시 손실 시 대량 fail. 토큰 소진 없음 (subprocess 단계 실패라 API 호출 전)
6. **Batch recover access violation (0xC0000005)**: 간헐 발생. 재실행으로 복구 가능
7. **WSL `/mnt/f/` I/O 느림**: PDF 분석 건당 70초. Windows 로컬 실행 권장

---

## 참조 문서

| 파일 | 내용 | 용도 |
|------|------|------|
| `docs/masterplan.md` | AI Agent 확장 설계 원본 — 3-Layer, Investment Chain, 크로스 리포트 연결, 포맷 전략 | Phase B/D/E 설계 레퍼런스 |
| `docs/LAYER2_DESIGN.md` | Layer 2 구현 스펙 — DB 스키마, 추출 파이프라인 | 구현 완료, 코드 레퍼런스 |
| `docs/ARCHITECTURE_CURRENT.md` | 현재 아키텍처 + 코드 vs 기획 차이점 | 주의사항 참조 |
| `docs/webapp-expansion-plan.md` | 매매 저널 + AI Agent 상세 스펙 | Phase C/D 상세 스펙 |
| `docs/webapp-expansion-progress.md` | **deprecated** — 이 ROADMAP으로 통합됨 | 참조 불필요 |
| `docs/HANDOFF.md` | 최근 세션 작업 내역 + 운영 상태 | 세션 시작 시 참조 |
| `docs/windows_setup.md` | Windows 머신 세팅 / 이관 가이드 | 새 머신 구축 시 |
| `docs/scheduler_setup.md` | Task Scheduler / launchd 등록 레퍼런스 | 스케줄 작업 등록 시 |

---

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

### 리스너 머신 (Windows)
`config/.env` 참조. 필수: `TELEGRAM_API_ID/HASH`, `DATABASE_URL`, `ANTHROPIC_API_KEY`, `GEMINI_API_KEY`, `PDF_BASE_PATH`.
