# Report Collector - 현재 아키텍처 및 로직 분석

> 작성일: 2026-03-13 → **최종 업데이트: 2026-03-31**
> 목적: 코드 상 실제 구현 로직 정리 (기획 대비 현황 파악용)

---

## 목차

1. [전체 흐름 요약](#1-전체-흐름-요약)
2. [진입점 4가지](#2-진입점-4가지)
3. [S1: 정규식 파싱](#3-s1-정규식-파싱)
4. [S2a: LLM 메시지 분류](#4-s2a-llm-메시지-분류)
5. [PDF 다운로드 및 아카이브](#5-pdf-다운로드-및-아카이브)
6. [분석 파이프라인 (6단계)](#6-분석-파이프라인-6단계)
7. [품질 판정 (parse_quality)](#7-품질-판정-parse_quality)
8. [DB 저장 및 중복 처리](#8-db-저장-및-중복-처리)
9. [DB 스키마](#9-db-스키마)
10. [Pipeline Status Flow](#10-pipeline-status-flow)
11. [API 엔드포인트](#11-api-엔드포인트)
12. [프론트엔드 화면](#12-프론트엔드-화면)
13. [LLM 비용 추적](#13-llm-비용-추적)
14. [동시 실행 규칙](#14-동시-실행-규칙)
15. [현재 구현 상태](#15-현재-구현-상태)
16. [주의사항](#16-주의사항)

---

## 1. 전체 흐름 요약

```
 수집 (Telegram)                         분석 (run_analysis.py, 독립 실행)
 ────────────────                        ─────────────────────────────────
 Telegram 메시지                          pdf_done 리포트 (DB 쿼리)
       │                                        │
       ▼                                        ▼
 [S1] 정규식 파싱                          ③ key_data (Gemini Flash-Lite)
       │                                   broker/analyst/date/stock/opinion
       ▼                                        │
 [S2a] LLM 분류 (Haiku)                        ▼
   ├─ broker_report → DB                  ② Markdown (PyMuPDF4LLM)
   ├─ news/general → skip                      │
   └─ ambiguous → pending                      ▼
       │                                  ④ 이미지 추출 (PyMuPDF)
       ▼                                        │
 PDF 다운로드                                    ▼
   ├─ Telegram document                   ⑤ 차트 디지타이징 (Gemini Flash-Lite)
   ├─ t.me 링크 → resolve                      │
   └─ HTTP URL fallback                        ▼
       │                                  ⑥ Layer2 (Sonnet Batch API, 50% 할인)
       ▼                                   report_category/thesis/chain/mentions
 pipeline_status = pdf_done                     │
                                                ▼
                                          pipeline_status = done
```

> **아키텍처 핵심 변경 (2026-03-19~31)**:
> - 수집과 분석 완전 분리 — 백필은 S2a+PDF만, 분석은 `run_analysis.py`로 독립
> - 기존 S2b → key_data(Gemini) + Layer2(Sonnet Batch)로 재편
> - Streaming batch: N건마다 Layer2 제출 → 중간 실패 시 손실 최소화

---

## 2. 진입점 4가지

| 진입점 | 파일 | 역할 |
|--------|------|------|
| **실시간 리스너** | `main.py` → `collector/listener.py` | Telegram 24/7 수집+PDF (분석은 동기 1건씩) |
| **백필** | `run_backfill.py` → `collector/backfill.py` | 히스토리 수집+PDF. forward/reverse 양방향 |
| **PDF 다운로드** | `run_download_pending.py` | DB 쿼리 기반 PDF 직접 다운로드 (Telegram 필요) |
| **분석** | `run_analysis.py` | pdf_done → key_data→markdown→charts→Layer2 Batch |

### 백필 옵션
```bash
python run_backfill.py --channel @sunstudy1004 --limit 5000    # 특정 채널, forward
python run_backfill.py --reverse --limit 15000                  # 최신→과거 역순
python run_backfill.py --retry-stage pdf_failed                 # PDF 재시도 (retryable만)
python run_backfill.py --retry-stage pdf_failed --all-failures  # 전체 실패 건 재시도
```

### 분석 옵션
```bash
python run_analysis.py --concurrency 8 --batch-size 100         # streaming batch
```

---

## 3. S1: 정규식 파싱

**파일**: `parser/registry.py`, `parser/base.py`, `parser/generic.py`

파서 3개가 우선순위 순으로 시도됨:

| 우선순위 | 파서 | 대상 채널 | 특징 |
|---------|------|----------|------|
| 1 | `RepostoryParser` | `@repostory123` | 가장 정교함 |
| 2 | `CompanyReportParser` | `@companyreport` | 중간 수준 |
| 3 | `GenericParser` | 모든 채널 (fallback) | 항상 성공 (None 반환 없음) |

**추출 결과 (`ParsedReport` dataclass)**: title, broker, stock_name/code, sector, opinion, target_price, pdf_url, parse_quality 등

**정규화 (`normalizer.py`)**: 증권사 별칭 통합, 투자의견 통합, 제목 정규화(`title_normalized`), 가격 파싱

---

## 4. S2a: LLM 메시지 분류

**파일**: `parser/llm_parser.py`
**모델**: `claude-haiku-4-5-20251001`
**방식**: tool_use (structured output), 최대 128 output tokens

| 라벨 | 의미 | 후속 처리 |
|------|------|----------|
| `broker_report` | 증권사 리포트 | DB 저장 → PDF |
| `news` | 뉴스, 시황 | skip |
| `general` | 광고, 스팸 | skip |
| `ambiguous` | 불명확 | pending_messages 저장 |

**실패 시 기본값**: `broker_report` (데이터 손실 방지 우선)

---

## 5. PDF 다운로드 및 아카이브

**파일**: `storage/pdf_archiver.py`

### 다운로드 경로 (fallback 체인)
1. Telegram document 직접 다운로드
2. t.me 링크 resolve → 실제 URL
3. HTTP URL 직접 다운로드

### 저장 경로
```
{pdf_base_path}/{YYYY}/{MM}/{YYYYMMDD}_{증권사}_{종목}_{정규화제목}.pdf
예: F:\report-collector\pdfs\2024\03\20240315_미래에셋_삼성전자_반도체업황개선지속.pdf
```

### 실패 처리
- `pdf_download_failed = True`, `pdf_fail_reason` (50자 truncate)
- `is_retryable_failure()`: 일시적 오류만 retry 대상으로 분류
- `--retry-stage pdf_failed`: retryable만 재시도
- `--all-failures`: 전체 실패 건 재시도

---

## 6. 분석 파이프라인 (6단계)

`run_analysis.py`에서 독립 실행. pdf_done 리포트 대상.

| 단계 | 파일 | 모델/도구 | 설명 |
|------|------|-----------|------|
| ③ key_data | `parser/key_data_extractor.py` | Gemini Flash-Lite | broker, analyst, date, stock, type, opinion 추출 |
| ② Markdown | `parser/markdown_converter.py` | PyMuPDF4LLM (60s timeout, pypdf fallback) | PDF → Markdown 텍스트 |
| ④ 이미지 추출 | `parser/image_extractor.py` | PyMuPDF | PDF 페이지별 이미지 추출 |
| ⑤ 차트 디지타이징 | `parser/chart_digitizer.py` | Gemini Flash-Lite | 차트 → 구조화 데이터, 품질 메트릭(q_chars, q_table_rows, q_digits) |
| ⑥ Layer2 | `parser/layer2_extractor.py` | Sonnet (Batch API) | 구조화 분석 (아래 상세) |
| 저장 | `storage/analysis_repo.py` | - | 단일 트랜잭션 저장 |

### Layer2 추출 결과
- `report_category`: stock / industry / macro
- `meta`: broker, stock_name, stock_code, opinion, target_price, analyst, title, report_type
- `thesis`: summary, sentiment (-1.0~1.0)
- `chain`: 투자 논리 체인 (step 타입별 인과관계)
- `stock_mentions`: 연관 종목 (primary/related, impact)
- `sector_mentions`: 연관 섹터
- `keywords`: 키워드 태그
- `extraction_quality`: high / medium / low

### Streaming Batch
- Phase 1 (key_data→markdown→charts)을 N건 처리할 때마다 Layer2 Batch 제출
- Sonnet Batch API 50% 할인 적용
- 중간 크래시 시 이미 제출된 배치는 보존

---

## 7. 품질 판정 (parse_quality)

**파일**: `parser/quality.py`

| 등급 | 조건 |
|------|------|
| `good` | broker + title + (stock_name/code OR 매크로 타입) |
| `partial` | broker + title, 종목 없음 & 매크로 아님 |
| `poor` | broker 없음 OR title 없음 |

---

## 8. DB 저장 및 중복 처리

**파일**: `storage/report_repo.py`

**Unique Constraint** (`uix_report_dedup`):
```
(broker, report_date, analyst, stock_name, title_normalized)
```

**Upsert 전략**: `ON CONFLICT DO UPDATE` — 새 데이터에 pdf_url/opinion 있을 때만 업데이트 (빈 값으로 덮어쓰기 방지)

**종목코드 보강** (`storage/stock_mapper.py`): stock_name → KRX code 캐시 매핑

---

## 9. DB 스키마

### 핵심 테이블

**reports** (45+ 컬럼)
- PK: `id` (BigInteger)
- 수집: `broker`, `report_date`, `analyst`, `stock_name`, `stock_code`, `title`, `title_normalized`
- PDF: `pdf_url`, `pdf_path`, `pdf_size_kb`, `page_count`, `pdf_download_failed`, `pdf_fail_reason`
- 파이프라인: `pipeline_status`, `s2a_label`, `parse_quality`
- 레거시: `ai_summary`, `ai_sentiment`, `ai_keywords` (미사용, Layer2로 대체)
- 인덱스: stock, stock_code, sector, broker, analyst, date, type, source, pdf_failed

**channels**
- `channel_username`, `last_message_id`, `reverse_min_id`, `last_collected_at`

**report_analysis** — Layer2 결과
- `report_id` (FK), `report_category`, `analysis_data` (JSONB), `llm_model`, `cost_usd`, `schema_version`, `extraction_quality`

**report_markdown** — Markdown 캐시
- `report_id` (FK unique), `markdown_text`, `converter`, `token_count`

**report_stock_mentions** / **report_sector_mentions** — 종목/섹터 연결

### 기타 테이블
| 테이블 | 역할 |
|--------|------|
| `pending_messages` | S2a ambiguous 메시지 (review_status: pending/broker_report/discarded) |
| `backfill_runs` | 백필 실행 이력 (channel, from/to id, n_scanned/saved/skipped) |
| `stock_codes` | KRX 종목 마스터 (code, name, market, sector) |
| `llm_usage` | LLM 비용 추적 (message_type: s2a/layer2/key_data/chart) |

### 최근 마이그레이션
- `v7695787af907`: `channels.reverse_min_id` 컬럼 추가 (reverse backfill 추적)

---

## 10. Pipeline Status Flow

```
new → s2a_done / s2a_skipped / s2a_failed
                    │
                    ▼
              pdf_done / pdf_failed
                    │
                    ▼
             analysis_pending
                    │
                    ▼
              done / analysis_failed
```

---

## 11. API 엔드포인트

**Base**: FastAPI (`api/main.py`), CORS 허용 (localhost:3000/3001, *.vercel.app)

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/reports` | 리포트 목록 (필터, 페이지네이션) |
| GET | `/api/reports/{id}` | 리포트 상세 |
| GET | `/api/reports/filters` | 드롭다운 옵션 |
| GET | `/api/analysis/{id}` | Layer2 분석 결과 |
| GET | `/api/stats/overview` | 전체 통계 |
| GET | `/api/stats/llm?days=30` | LLM 비용 현황 |
| GET | `/api/stats/backfill` | 채널별 백필 상태 |
| POST | `/api/backfill/run` | 백필 트리거 |
| GET/POST | `/api/channels` | 채널 관리 |
| GET/POST | `/api/pending` | ambiguous 메시지 검토 |
| GET | `/api/stocks` | 종목코드 조회 |
| POST | `/api/agent` | AI 에이전트 채팅 |
| GET | `/api/trades` | 거래 분석 |

---

## 12. 프론트엔드 화면

**프레임워크**: Next.js (`web/`), Vercel 배포

| 페이지 | 경로 | 내용 |
|--------|------|------|
| 홈 (리포트 목록) | `/` | 검색/필터/페이지네이션 (ReportTable, ReportFilters) |
| 리포트 상세 | `/reports/[id]` | 메타데이터 + Layer2 분석 (Layer2Section) |
| 통계 대시보드 | `/stats` | 수집 현황, 비용, 커버리지 |
| 백필 관리 | `/backfill` | 채널 관리 + 백필 트리거 |
| Pending 검토 | `/pending` | ambiguous 메시지 수동 분류 |

---

## 13. LLM 비용 추적

| 모델 | 용도 | Input (per 1M) | Output (per 1M) |
|------|------|----------------|-----------------|
| claude-haiku-4-5-20251001 | S2a 분류 | $0.80 | $4.00 |
| claude-sonnet-4-6 | Layer2 (Batch 50% 할인) | $3.00 | $15.00 |
| gemini-2.5-flash-lite | key_data, charts | $0.10 | $0.40 |

> **비용 절감**: Flash($0.30/$2.50) → Flash-Lite($0.10/$0.40)로 ~85% 절감

**기록**: `llm_usage` 테이블 — model, input/output_tokens, cost_usd, message_type(s2a/layer2/key_data/chart)

---

## 14. 동시 실행 규칙

| 조합 | 가능? | 이유 |
|------|-------|------|
| analysis + backfill | O | Telegram vs Gemini/Anthropic |
| analysis + download_pending | O | DB만 겹침 |
| backfill + download_pending | X | Telegram 세션 충돌 |
| analysis + backfill_dates | X | 둘 다 Gemini API 사용 |

---

## 15. 현재 구현 상태

| 기능 | 상태 | 비고 |
|------|------|------|
| Telegram 실시간 리스너 | ✅ | StringSession 지원 |
| S1 정규식 파싱 (3종) | ✅ | repostory, companyreport, generic |
| S2a LLM 분류 | ✅ | Haiku |
| DB Upsert + 중복 처리 | ✅ | |
| PDF 다운로드 & 아카이브 | ✅ | Telegram/t.me/URL fallback |
| PDF → Markdown | ✅ | PyMuPDF4LLM + pypdf fallback |
| key_data 추출 | ✅ | Gemini Flash-Lite |
| 차트 디지타이징 | ✅ | Gemini Flash-Lite, 품질 메트릭 |
| Layer2 구조화 추출 | ✅ | Sonnet Batch API |
| Streaming batch | ✅ | N건마다 Layer2 제출 |
| Backfill forward | ✅ | Phase 0.5 (기존 건 skip) |
| Backfill reverse | ✅ | reverse_min_id 추적 |
| PDF 직접 다운로드 | ✅ | run_download_pending.py |
| report_date 보정 | ✅ | scripts/backfill_dates.py |
| API 서버 (FastAPI) | ✅ | Railway |
| 웹 프론트엔드 (Next.js) | ✅ | Vercel |
| 채널 관리 (웹) | ✅ | |
| LLM 비용 추적 | ✅ | |
| Pending 검토 UI | ✅ | |
| ~~S2b 메타데이터~~ | ⛔ | Layer2 + key_data로 대체 |
| ~~Stage5 PDF 분석~~ | ⛔ | Layer2로 대체 |
| new 건 S2a 분류 | 🔄 | 2,289건, 스크립트 미구현 |
| 이미지 사전 필터링 | 🔄 | 유료 전환 후 재테스트 필요 |

---

## 16. 주의사항

1. **GenericParser 항상 성공**: None 반환 안 함 → quality="poor" 데이터 대량 가능
2. **S2a 실패 = broker_report**: 데이터 수집 우선 전략
3. **Markdown 30K자 제한**: Layer2 입력 시 truncate, 긴 리포트 뒷부분 분석 안 됨
4. **중복 키에 analyst 포함**: 같은 리포트 다른 채널 수집 시 analyst NULL vs 실명 차이로 중복 발생 가능
5. **해외 종목 stock_code 빈 값**: KRX 코드 없어 `company_name[:20]` 대리 키 사용
6. **레거시 코드 잔존**: S2b(`extract_metadata`), Stage5(`pdf_analyzer.py`) 코드 남아있으나 미사용
7. **pending_messages 검토 후 재처리 없음**: broker_report 분류해도 자동 파이프라인 트리거 안 됨
8. **DB 커넥션 풀**: `pool_pre_ping=True`, `pool_recycle=300` (Railway idle timeout 대응)
9. **pdf_fail_reason**: VARCHAR(50) truncate 적용
10. **_REPORT_TIMEOUT**: 분석 단건 1800초 (이전 300초에서 상향)
