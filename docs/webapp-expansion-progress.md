# Web App 확장 진행 상황

> **⚠️ Deprecated**: 이 파일은 2026-03-20 이후 갱신되지 않음. 진행 상태는 `plans/ROADMAP.md`에서 통합 관리.
>
> ~~마지막 업데이트: 2026-03-20~~
> ~~전체 플랜: [webapp-expansion-plan.md](./webapp-expansion-plan.md)~~

---

## 전체 현황

| Phase | 이름 | 상태 | 비고 |
|-------|------|------|------|
| 0 | 인프라 정비 | **대기** | |
| 1 | 매매 저널 — DB + 백엔드 | **대기** | CSV 샘플 미확보 (파서는 보류) |
| 2 | 기술지표 + 매칭 | **대기** | Phase 1 완료 후 |
| 3 | 매매 저널 — 프론트엔드 | **대기** | Phase 1-2 완료 후 |
| 4 | AI Agent 챗봇 | **대기** | Phase 1-3 완료 후 |
| 5 | 크로스 모듈 연동 | **대기** | Phase 3-4 완료 후 |
| 6 | 모바일 + PWA | **대기** | 마지막 |

---

## Phase 0: 인프라 정비

- [ ] Alembic DB ↔ migration 이력 동기화 확인
- [ ] `layout.tsx` 네비게이션 재구성 (리포트 | 매매 | AI Agent | 설정)
- [ ] `StatCard` 컴포넌트 shared로 추출

## Phase 1: 매매 저널 — DB + 백엔드

- [ ] `db/models.py` — Trade, TradeIndicator, TradePair 모델
- [ ] Alembic 마이그레이션 생성 + 실행
- [ ] `trades/csv_parsers/common.py` — TradeRow, 인코딩 감지, 종목코드 정규화
- [ ] `trades/csv_parsers/` — 브로커별 파서 스텁
- [ ] `trades/trade_repo.py` — upsert, 목록, 통계 쿼리
- [ ] `api/routers/trades.py` — 엔드포인트 7개
- [ ] **CSV 샘플 확보** — 미래에셋 / 키움 / 삼성
  - [ ] 미래에셋 CSV
  - [ ] 키움 CSV
  - [ ] 삼성 CSV
- [ ] 브로커별 파서 상세 구현 (샘플 확보 후)

## Phase 2: 기술지표 + 매칭

- [ ] `trades/indicators.py` — pykrx OHLCV + pandas_ta 지표 계산
- [ ] `trades/pairing.py` — FIFO 매수-매도 매칭
- [ ] BackgroundTask 비동기 지표 계산

## Phase 3: 매매 저널 — 프론트엔드

- [ ] `trades/upload/page.tsx` — CSV 업로드 + 프리뷰
- [ ] `trades/page.tsx` — 체결 목록 + 인라인 메모
- [ ] `trades/chart/[symbol]/page.tsx` — 캔들차트 + 마커 + 보조지표
- [ ] `trades/stats/page.tsx` — 성과 대시보드
- [ ] `trades/review/page.tsx` — 복기 미작성 필터

## Phase 4: AI Agent 챗봇

- [ ] `agent/context_builder.py` — SQL → YAML 컨텍스트 조립
- [ ] `agent/prompt_templates.py` — 시스템 프롬프트 (리포트 기반 + 일반지식 명시)
- [ ] `agent/chat_handler.py` — Claude 스트리밍 + 비용 추적
- [ ] `api/routers/agent.py` — SSE 엔드포인트
- [ ] `web/app/agent/page.tsx` — 챗봇 UI

## Phase 5: 크로스 모듈 연동

- [ ] 매매 차트 → 관련 리포트 표시
- [ ] 리포트 상세 → 매매 내역 표시
- [ ] 대시보드 통합 타임라인

## Phase 6: 모바일 + PWA

- [ ] manifest.json + 서비스워커
- [ ] 모바일 반응형 (햄버거, 하단 탭바)
- [ ] 차트 full-width + 터치 제스처
- [ ] 44px 터치 타겟

---

## 미결정 사항

| 항목 | 상태 | 메모 |
|------|------|------|
| CSV 샘플 | **블로커** | HTS에서 내보내기 필요 |
| Stochastic 파라미터 | 미결정 | K/D/slowing 값 확인 필요 |
| 매매 매칭 전략 | 미결정 | FIFO 기본, 물타기 케이스 조정 가능 |
| 계좌별 통계 분리 | 미결정 | 위탁/퇴직연금 별도 vs 통합 |

## 세션 로그

| 날짜 | 세션 | 작업 내용 |
|------|------|-----------|
| 2026-03-20 | #1 | 전체 플래닝 완료. `docs/webapp-expansion-plan.md` 작성 |
