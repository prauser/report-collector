# Report Collector → AI Agent 확장 설계 문서

> 작성일: 2026-03-14 (구현 완료: 2026-03-15)
> 목적: 기존 report-collector 시스템을 AI Agent 기반 분석 시스템으로 확장하기 위한 설계안
> 상태: **구현 완료** — DB 스키마 적용, 파이프라인 통합, 20건 테스트 완료

---

## 1. 현재 시스템 개요

텔레그램 채널에서 증권 리포트를 수집하여 기초 메타데이터를 저장하고 PDF 원본을 아카이빙하는 시스템.

### 현재 저장 데이터 (Layer 1)

- 증권사, 애널리스트명, 종목명, 투자의견, 제목, 목표가
- PDF 원본 아카이빙
- 수집 채널 정보, 텔레그램 메시지 ID

### 현재 기술 스택

- Python 기반
- LLM: Claude API (향후 OpenAI/Gemini 교체 가능성)
- DB: Railway PostgreSQL
- 마이그레이션 도구: alembic (설치되어 있으나 미사용)

### 현재 DB 주요 테이블

| 테이블 | 역할 |
|--------|------|
| `reports` | 리포트 메타데이터 (35개 컬럼) |
| `stock_codes` | 종목 마스터 |
| `price_history` | 목표가/의견 이력 |
| `change_events` | 의견 변경 이벤트 |
| `channels` | 텔레그램 채널 관리 |
| `pending_messages` | 처리 대기 메시지 |
| `llm_usage` | LLM 사용량 추적 |
| `backfill_runs` | 백필 실행 이력 |

---

## 2. 목표: AI Agent 분석 시스템

사용자가 특정 기업에 대해 질문하면, 수집된 리포트를 기반으로 종합 분석하여 답변하는 AI Agent.

### 예상 질의 유형

- "삼성전자 최근 투자의견 변화와 그 이유를 알려줘"
- "2차전지 업종 전망과 주목할 종목은?"
- "금리 인하가 어떤 섹터에 영향을 줄까?"
- "에코프로비엠 매출구조 변화 추이를 분석해줘"

### 핵심 요구사항

- 단순 조회가 아닌 **크로스 리포트 종합분석** (종목 + 산업 + 매크로 리포트를 연결)
- 투자의견 상향/하향의 **주된 주장 요인**까지 파악
- 매출구조 변화, 밸류에이션 논거 등 **논리 체인** 추적

---

## 3. 3-Layer 데이터 아키텍처

### 설계 원칙

수집 시점에 리포트 한 건의 **사실(fact)을 구조화 추출**하되, 여러 리포트에 걸친 **종합 판단은 Agent 질의 시점에 수행**.

```
Layer 1: 메타데이터 인덱스 (현재 reports 테이블)
    → 검색, 필터링용

Layer 2: 구조화된 상세 분석 (신규 report_analysis 테이블)
    → 투자 논리 체인, 재무 추정, 산업 맥락
    → Agent 분석의 핵심 소스

Layer 3: PDF 원문 아카이브 (현재 pdf_path)
    → 근거 확인용 fallback
```

### Layer 2를 별도 테이블로 분리하는 이유

1. 기존 `reports` 테이블이 이미 컬럼 35개로 비대함
2. 스키마 변경 시 분석 테이블만 재처리 가능 (수집 로직에 영향 없음)
3. 수집과 분석의 생명주기가 다름 (수집은 즉시, 분석은 재처리 가능)

---

## 4. 리포트 분류 체계

LLM이 리포트를 3가지 타입으로 분류하고, 타입별 다른 스키마로 추출.

| 타입 | 설명 | 예시 |
|------|------|------|
| `stock` | 특정 종목 분석 | "삼성전자 - AI 반도체 수혜 본격화" |
| `industry` | 산업/섹터 분석 | "2차전지 - 유럽 EV 보조금 재개의 수혜 구조" |
| `macro` | 거시경제/정책 | "FOMC 프리뷰 - 6월 인하 시작 전망" |

### 파이프라인 변경 (S2b + Stage5 → 통합 Layer2 추출)

기존 S2a(분류) 이후 S2b(메타추출)와 Stage5(PDF분석)를 **단일 Layer2 추출 호출로 통합**.

```
[기존]  S2a → S2b(Haiku) → DB → PDF다운 → Stage5(Sonnet) → DB
[변경]  S2a → PDF다운 → Markdown변환 → Layer2 추출(Sonnet, 1회) → DB(전부 한번에)
```

---

## 5. Layer 2 스키마 설계

### 5.1 핵심 설계: 인과관계 체인 (Investment Chain)

단편적 요약이 아닌, 애널리스트의 **논리 흐름 자체를 보존**하는 구조.

#### step 타입 (고정 enum)

| step 타입 | 설명 | 사용 컨텍스트 |
|-----------|------|---------------|
| `trigger` | 논리의 출발점 (이벤트, 정책 등) | 공통 |
| `mechanism` | trigger가 작동하는 메커니즘 | 공통 |
| `demand_transmission` | 수요 전달 경로 | 산업 |
| `supply_dynamics` | 공급 측 동학 | 산업 |
| `pricing_impact` | 가격/마진 영향 | 산업 |
| `financial_impact` | 재무적 영향 (실적 변화) | 종목/산업 |
| `valuation_impact` | 밸류에이션 영향 | 종목 |
| `structural_risk` | 구조적 리스크 | 공통 |
| `uncertainty` | 불확실성 요인 | 공통 |
| `data_signal` | 데이터 시그널 (경제지표 등) | 매크로 |
| `policy_logic` | 정책 논리/방향성 | 매크로 |
| `market_transmission` | 시장 전달 경로 | 매크로 |
| `local_impact` | 국내 시장 영향 | 매크로 |

(종목/산업/매크로별 상세 JSON 스키마 예시는 원문 참조)

---

## 6. DB 스키마

SQL 파일: `docs/schema_layer2.sql`

### 신규 테이블 6개
- `report_markdown` — PDF → Markdown 변환 결과
- `report_analysis` — Layer 2 핵심 (analysis_data JSONB)
- `report_stock_mentions` — 종목-리포트 다대다 매핑
- `report_sector_mentions` — 섹터-리포트 매핑
- `report_keywords` — 키워드 태그
- `analysis_jobs` — 처리 로그

### 기존 테이블 변경
- `reports`에 `analysis_status`, `analysis_version`, `markdown_converted` 추가

### SQL 리뷰 결과 (수정 필요)
1. **v_stock_latest_analysis 뷰**: `r.company_name` → `r.stock_name` (컬럼명 불일치)
2. **중복 인덱스 제거 권장**: `idx_report_markdown_report_id`, `idx_report_analysis_report_id`, `idx_stock_codes_code`
3. **GIN 인덱스**: 개별 필드 인덱스(ticker, sector) 제거하고 catch-all `jsonb_path_ops` 유지 권장
4. **analysis_jobs에 target_schema_version 컬럼 추가 권장**

---

## 7. 비용 추정

리포트 한 건당 ~$0.02-0.06, 월 450건 기준 ~$18/월
