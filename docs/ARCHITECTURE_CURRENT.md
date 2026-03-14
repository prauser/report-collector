# Report Collector - 현재 아키텍처 및 로직 분석

> 작성일: 2026-03-13 (Layer 2 반영: 2026-03-15)
> 목적: 코드 상 실제 구현 로직 정리 (기획 대비 현황 파악용)

---

## 목차

1. [전체 흐름 요약](#1-전체-흐름-요약)
2. [S1: 정규식 파싱](#2-s1-정규식-파싱)
3. [S2a: LLM 메시지 분류](#3-s2a-llm-메시지-분류)
4. [S2b: LLM 메타데이터 추출](#4-s2b-llm-메타데이터-추출)
5. [품질 판정 (parse_quality)](#5-품질-판정-parse_quality)
6. [DB 저장 및 중복 처리](#6-db-저장-및-중복-처리)
7. [PDF 다운로드 및 아카이브](#7-pdf-다운로드-및-아카이브)
8. [PDF AI 분석 (S5)](#8-pdf-ai-분석-s5)
9. [데이터 수집 방식](#9-데이터-수집-방식)
10. [DB 스키마](#10-db-스키마)
11. [API 엔드포인트](#11-api-엔드포인트)
12. [프론트엔드 화면](#12-프론트엔드-화면)
13. [LLM 비용 추적](#13-llm-비용-추적)
14. [현재 구현 상태 (✅/🔄)](#14-현재-구현-상태)
15. [코드 vs 기획 차이점 / 주의사항](#15-코드-vs-기획-차이점--주의사항)

---

## 1. 전체 흐름 요약

```
Telegram 채널 메시지
        │
        ▼
[S1] 정규식 파싱 (채널별 parser)
        │ ParsedReport
        ▼
[S2a] LLM 분류 (Claude Haiku)
    ├─ broker_report → 계속
    ├─ news / general → ❌ 건너뜀
    └─ ambiguous → pending_messages 저장 (사람 검토)
        │
        ▼
[품질 판정] parse_quality: good / partial / poor
        │
        ▼
[DB Upsert] reports 테이블
        │
        ├─ pdf_url 있으면
        │   └─ PDF 다운로드 → Markdown 변환 (PyMuPDF4LLM)
        │
        ▼
[Layer2] 구조화 추출 (Claude Sonnet, 1회 호출)
    → 리포트 분류 (stock/industry/macro)
    → 투자 논리 체인 (Investment Chain)
    → 메타데이터 (broker, stock, opinion 등)
    → 연관 종목/섹터/키워드
        │
        ▼
[DB 저장] report_analysis + stock_mentions + sector_mentions + keywords
```

> **파이프라인 변경 (2026-03-15)**: 기존 `S2b(Haiku 메타추출) + Stage5(Sonnet PDF분석)`을
> 단일 `Layer2 추출(Sonnet 1회)`로 통합. S2b/Stage5 코드는 아직 잔존하나 미사용.

**수집 진입점 2가지**
- 실시간 리스너: `collector/listener.py` (Telethon, 24/7)
- 백필 스크립트: `collector/backfill.py` (과거 메시지 수집)

---

## 2. S1: 정규식 파싱

**파일**: `parser/registry.py`, `parser/base.py`, `parser/repostory.py`, `parser/companyreport.py`, `parser/generic.py`

파서 3개가 우선순위 순으로 시도됨:

| 우선순위 | 파서 | 대상 채널 | 특징 |
|---------|------|----------|------|
| 1 | `RepostoryParser` | `@repostory123` | 가장 정교함. 종목명/코드, 증권사, 제목, 애널리스트, 목표가, 의견 추출 |
| 2 | `CompanyReportParser` | `@companyreport` | 중간 수준. 비슷한 필드 다른 패턴 |
| 3 | `GenericParser` | 모든 채널 (fallback) | 최소한의 정보만 추출. 항상 성공 (None 반환 없음) |

**추출 결과 (`ParsedReport` dataclass)**:
```
- title, source_channel, raw_text, source_message_id
- broker (증권사)
- stock_name, stock_code, sector
- opinion, target_price, prev_opinion, prev_target_price
- earnings_quarter, est_revenue, est_op_profit, est_eps
- pdf_url
- parse_quality ("good"/"partial"/"poor")
- parse_errors (list)
```

**정규화 (`normalizer.py`)**:
- 증권사 별칭 통합: "미래에셋" → "미래에셋증권"
- 투자의견 통합: "매수", "BUY" → 단일 표준
- 제목 정규화: 특수문자 제거, 소문자화 (중복 제거용 `title_normalized`)
- 가격 파싱: "85,000원", "8.5만" → `85000` (int)

---

## 3. S2a: LLM 메시지 분류

**파일**: `parser/llm_parser.py`
**모델**: `claude-haiku-4-5-20251001`
**방식**: tool_use (structured output)
**토큰**: 최대 128 output tokens

**분류 카테고리**:

| 라벨 | 의미 | 후속 처리 |
|------|------|----------|
| `broker_report` | 증권사 리포트 (종목/매크로) | S2b로 진행 |
| `news` | 뉴스, 시황 공지, 공지 | ❌ 건너뜀 |
| `general` | 광고, 채널 안내, 스팸 | ❌ 건너뜀 |
| `ambiguous` | 불명확, 텍스트 손상, PDF만 있는 경우 | pending_messages에 저장 |

**실패 시 기본값**: `broker_report` → 데이터 손실 방지 우선

---

## 4. ~~S2b: LLM 메타데이터 추출~~ → Layer 2로 통합

> **변경**: S2b는 Layer 2 추출로 통합되어 더 이상 사용하지 않습니다.
> 코드 (`parser/llm_parser.py`의 `extract_metadata`)는 아직 잔존하지만 호출하지 않음.
> 메타데이터 추출은 Layer 2의 `meta` 블록에서 처리됩니다.

### Layer 2 구조화 추출

**파일**: `parser/layer2_extractor.py`
**모델**: `claude-sonnet-4-6` (`settings.llm_pdf_model`)
**방식**: tool_use (structured output)
**토큰**: 최대 4096 output tokens
**입력**: 메시지 텍스트 + PDF Markdown (30K자 제한)

**추출 결과**:
- `report_category`: stock / industry / macro
- `meta`: broker, stock_name, stock_code, opinion, target_price, analyst, title, report_type
- `thesis`: summary, sentiment (-1.0~1.0)
- `chain`: 투자 논리 체인 (step 타입별 인과관계)
- `stock_mentions`: 연관 종목 리스트 (primary/related, impact)
- `sector_mentions`: 연관 섹터 리스트
- `keywords`: 키워드 태그
- `extraction_quality`: high / medium / low

**PDF → Markdown 변환**: `parser/markdown_converter.py`
- PyMuPDF4LLM 기본, pypdf fallback
- `convert_pdf_to_markdown(pdf_path) -> (text, converter_name)`

**분석 결과 저장**: `storage/analysis_repo.py`
- 단일 트랜잭션으로 report_analysis + stock/sector_mentions + keywords 저장
- DELETE + INSERT 패턴 (멱등성 보장)
- reports.analysis_status = 'done' 업데이트

**실패 시**: `analysis_status = 'failed'`, analysis_jobs에 에러 로그 기록

---

## 5. 품질 판정 (parse_quality)

**파일**: `parser/quality.py`

| 등급 | 조건 |
|------|------|
| `good` | broker ✅ + title ✅ + (stock_name/code ✅ OR 매크로 타입) |
| `partial` | broker ✅ + title ✅, but 종목 없음 & 매크로도 아님 |
| `poor` | broker 없음 OR title 없음 |

**매크로 타입** (종목 불필요): 시황/전략, 채권/금리, 공모주, 매매동향, 경제/시황

---

## 6. DB 저장 및 중복 처리

**파일**: `storage/report_repo.py`

**Unique Constraint** (`uix_report_dedup`):
```
(broker, report_date, analyst, stock_name, title_normalized)
```

**Upsert 전략**:
```sql
ON CONFLICT (uix_report_dedup)
DO UPDATE SET pdf_url, opinion, target_price, raw_text, source_channel
WHERE 새 데이터에 pdf_url 또는 opinion 있을 때만
```
→ 빈 데이터로 기존 정보 덮어쓰는 것 방지.

**종목코드 보강** (`storage/stock_mapper.py`):
- S2b에서 stock_name 있으나 code 없을 때 KRX 코드 매핑 캐시 조회
- 없으면 NULL 유지 (나중에 보강 가능)

---

## 7. PDF 다운로드 및 아카이브

**파일**: `storage/pdf_archiver.py`

**저장 경로 구조**:
```
{YYYY}/{MM}/{YYYYMMDD}_{증권사}_{종목or산업}_{정규화제목}.pdf
예: 2024/03/20240315_미래에셋_삼성전자_반도체업황개선지속.pdf
```

**처리 순서**:
1. aiohttp로 다운로드 (User-Agent 헤더 포함, timeout 60s)
2. `settings.pdf_base_path` (기본: `./data/pdfs`)에 저장
3. pymupdf(fitz)로 페이지 수 추출
4. 실패 시 `pdf_download_failed = True` 플래그 설정

---

## 8. ~~PDF AI 분석 (S5)~~ → Layer 2로 통합

> **변경**: Stage5 PDF 분석은 Layer 2 추출로 통합되어 더 이상 사용하지 않습니다.
> 기존 `ai_summary`, `ai_sentiment`, `ai_keywords` 컬럼은 보존하되 신규 데이터에는 미사용.
> Layer 2의 `thesis.summary`, `thesis.sentiment`, `keywords`가 대체합니다.

---

## 9. 데이터 수집 방식

### 실시간 리스너 (`collector/listener.py`)
- Telethon으로 Telegram 연결
- Railway 배포: `telegram_session_string` (StringSession) 사용
- 지정된 채널의 신규 메시지 이벤트 핸들러 등록
- S1 → S2a → S2b → 품질 → DB → PDF 순서대로 처리

### 백필 (`collector/backfill.py`)
- `channels.last_message_id` 기준으로 이전 메시지 역순 수집
- 동일한 파이프라인 적용
- `backfill_runs` 테이블에 실행 이력 기록
- FloodWaitError 대응: 지정 시간 대기 후 재개
- API에서 `/api/backfill/run`으로 트리거 가능 (백그라운드 실행)

---

## 10. DB 스키마

### reports (핵심 테이블)
| 컬럼 | 타입 | 설명 |
|------|------|------|
| id | BigInteger PK | 자동증가 |
| broker | String | 증권사명 |
| report_date | Date | 리포트 날짜 |
| analyst | String | 애널리스트 |
| stock_name | String | 종목명 |
| stock_code | String | 종목코드 |
| title | String | 제목 |
| title_normalized | String | 중복 제거용 정규화 제목 |
| sector | String | 섹터 |
| report_type | String | 리포트 타입 |
| opinion | String | 투자의견 |
| target_price | Integer | 목표주가 |
| prev_opinion | String | 이전 의견 |
| prev_target_price | Integer | 이전 목표가 |
| earnings_quarter | String | 실적 분기 |
| est_revenue / est_op_profit / est_eps | Numeric | 추정 실적 |
| pdf_url | String | 원본 PDF URL |
| pdf_path | String | 로컬 저장 경로 |
| pdf_size_kb | Numeric | PDF 크기 |
| page_count | Integer | 페이지 수 |
| pdf_download_failed | Boolean | 다운로드 실패 여부 |
| parse_quality | String | good/partial/poor |
| source_channel | String | 수집 채널 |
| source_message_id | BigInteger | Telegram 메시지 ID |
| raw_text | Text | 원본 메시지 |
| ai_summary | Text | AI 요약 |
| ai_sentiment | Numeric(3,2) | AI 감성 점수 |
| ai_keywords | ARRAY | AI 키워드 |
| ai_processed_at | DateTime | AI 처리 시각 |
| collected_at / created_at / updated_at | DateTime | 시각 |

### Layer 2 테이블 (2026-03-15 추가)
| 테이블 | 역할 |
|--------|------|
| `report_markdown` | PDF → Markdown 변환 결과 (캐시) |
| `report_analysis` | Layer 2 핵심 (report_category, analysis_data JSONB) |
| `report_stock_mentions` | 종목-리포트 다대다 매핑 |
| `report_sector_mentions` | 섹터-리포트 매핑 |
| `report_keywords` | 키워드 태그 |
| `analysis_jobs` | 분석 처리 로그 (성공/실패 이력) |

### 기타 테이블
| 테이블 | 역할 |
|--------|------|
| `stock_codes` | KRX 종목코드 마스터 (name, code, market, sector) |
| `channels` | 수집 채널 목록 + last_message_id |
| `pending_messages` | S2a ambiguous 메시지 (수동 검토 대기) |
| `backfill_runs` | 백필 실행 이력 |
| `llm_usage` | LLM API 호출 비용 추적 |

---

## 11. API 엔드포인트

**Base**: `https://report-collector-production.up.railway.app`

| Method | Path | 설명 |
|--------|------|------|
| GET | `/api/reports` | 리포트 목록 (필터, 페이지네이션) |
| GET | `/api/reports/{id}` | 리포트 상세 (AI 필드 포함) |
| GET | `/api/reports/filters` | 드롭다운 옵션 (증권사, 의견 등) |
| GET | `/api/stats/overview` | 전체 통계 |
| GET | `/api/stats/llm?days=30` | LLM 비용 현황 |
| GET | `/api/stats/backfill` | 채널별 백필 상태 |
| GET | `/api/backfill/channels` | 백필 채널 목록 |
| POST | `/api/backfill/run` | 백필 실행 트리거 |
| GET | `/api/channels` | 채널 목록 |
| POST | `/api/channels` | 채널 추가 |
| PATCH | `/api/channels/{id}/toggle` | 채널 활성화/비활성화 |
| DELETE | `/api/channels/{id}` | 채널 삭제 |
| GET | `/api/pending` | ambiguous 메시지 목록 |
| POST | `/api/pending/{id}/resolve` | 수동 검토 처리 |
| GET | `/api/pending/stats` | 검토 대기 통계 |

---

## 12. 프론트엔드 화면

**URL**: `https://report-collector.vercel.app`

| 페이지 | 경로 | 내용 |
|--------|------|------|
| 홈 (리포트 목록) | `/` | 검색 + 필터 + 페이지네이션 (30개/페이지) |
| 리포트 상세 | `/reports/[id]` | 메타데이터 전체 + AI 요약/키워드/감성 |
| 통계 대시보드 | `/stats` | 수집 현황, 비용, 채널별 커버리지 |
| 백필 관리 | `/backfill` | 채널 관리 + 백필 트리거 |
| Pending 검토 | `/pending` | ambiguous 메시지 수동 분류 |
| 설정 | `/settings` | (현재 placeholder) |

---

## 13. LLM 비용 추적

**모델별 단가**:
| 모델 | 용도 | Input (per 1M) | Output (per 1M) |
|------|------|----------------|-----------------|
| claude-haiku-4-5-20251001 | S2a 분류 + S2b 추출 | ~$0.80 | ~$4.00 |
| claude-sonnet-4-6 | PDF 분석 | ~$3.00 | ~$15.00 |

**기록 항목** (`llm_usage` 테이블):
- model, purpose (s2a_classify / pdf_analysis)
- input_tokens, output_tokens, cost_usd
- message_type (S2a 결과), report_id, source_channel
- called_at

---

## 14. 현재 구현 상태

| 기능 | 상태 | 비고 |
|------|------|------|
| Telegram 실시간 리스너 | ✅ 완료 | StringSession 지원 |
| S1 정규식 파싱 (3종 파서) | ✅ 완료 | repostory, companyreport, generic |
| S2a LLM 분류 | ✅ 완료 | Haiku |
| ~~S2b LLM 메타데이터 추출~~ | ⛔ Layer2로 통합 | 코드 잔존, 미사용 |
| parse_quality 판정 | ✅ 완료 | good/partial/poor |
| DB Upsert + 중복 처리 | ✅ 완료 | |
| PDF 다운로드 & 아카이브 | ✅ 완료 | |
| ~~PDF AI 분석 (요약/감성/키워드)~~ | ⛔ Layer2로 통합 | 코드 잔존, 미사용 |
| PDF → Markdown 변환 | ✅ 완료 | PyMuPDF4LLM |
| Layer 2 구조화 추출 | ✅ 완료 | Sonnet 1회, 투자 논리 체인 |
| Layer 2 분석 저장 | ✅ 완료 | 6개 테이블 트랜잭션 |
| Layer 2 통계 대시보드 | ✅ 완료 | 웹에서 확인 가능 |
| 백필 (과거 메시지) | ✅ 완료 | |
| API 서버 (FastAPI) | ✅ 완료 | Railway |
| 웹 프론트엔드 (Next.js) | ✅ 완료 | Vercel |
| 채널 관리 (웹에서) | ✅ 완료 | |
| LLM 비용 추적 | ✅ 완료 | |
| KRX 종목코드 마스터 | 🔄 부분 | init_stock_codes.py 있으나 DB 적재 상태 미확인 |
| Pending 메시지 검토 UI | ✅ 완료 | |
| 설정 페이지 | 🔄 미구현 | placeholder |

---

## 15. 코드 vs 기획 차이점 / 주의사항

### ⚠️ 주의할 것들

1. **S1 파서 우선순위**: GenericParser는 항상 성공(None 반환 안 함) → S2a 없어도 무조건 DB에 들어감. quality="poor"인 데이터 대량 발생 가능.

2. **S2a 실패 기본값 = broker_report**: LLM 호출 실패 시 모든 메시지를 리포트로 분류. 비용 절감보다 데이터 수집 우선 전략.

3. **Markdown 30K자 제한**: Layer2 추출 시 PDF Markdown을 30,000자로 truncate. 긴 리포트는 뒷부분 분석 안 됨.

4. **Layer2 sentiment**: `thesis.sentiment` (-1.0~1.0). 기존 `ai_sentiment` Numeric(3,2) 컬럼은 신규 데이터에서 미사용.

5. **중복 판정 키에 analyst 포함**: 같은 리포트를 다른 채널에서 수집해도 analyst가 있으면 중복으로 안 잡힐 수 있음 (analyst NULL vs 실명 다름).

6. **Layer2 분석은 PDF 다운로드 성공 시 더 정확**: PDF 없어도 메시지 텍스트만으로 추출 시도하지만 `extraction_quality = "low"`. 재분석은 `scripts/run_analysis.py --reprocess`로 가능.

7. **해외 종목 stock_code 빈 값**: 해외 종목은 KRX 코드가 없어 stock_code가 비어있음. `company_name[:20]`을 대리 키로 사용하여 UNIQUE 제약 우회. 동명 해외 종목이 있으면 충돌 가능.

8. **backfill은 API에서 트리거 가능하지만 상태는 in-memory**: 서버 재시작 시 running 상태 초기화. BackfillRun 테이블로 실제 진행 상태 확인 필요.

9. **settings 페이지 미구현**: 웹에서 설정 변경 불가 (환경변수로만 관리).

10. **stock_codes 테이블 미확인**: KRX 종목 마스터가 제대로 적재되어 있지 않으면 stock_code 보강 안 됨.

11. **pending_messages 검토 후 재처리 없음**: "broker_report"로 분류해도 자동으로 Layer2 파이프라인이 다시 트리거되지 않음. 수동 처리 필요.

12. **레거시 코드 잔존**: S2b(`extract_metadata`), Stage5(`pdf_analyzer.py`), `scripts/analyze_pdfs.py` 등이 아직 코드에 남아있으나 미사용. 정리 예정.
