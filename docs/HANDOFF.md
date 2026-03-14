# Layer 2 구현 완료 핸드오프

> 작성: 2026-03-15
> 목적: Layer 2 구현 완료 후 남은 작업과 현재 상태 정리

---

## 참조 문서

| 파일 | 내용 |
|------|------|
| `docs/LAYER2_DESIGN.md` | 전체 설계 문서 (3-Layer 아키텍처, 체인 스키마, 비용 추정) |
| `docs/schema_layer2.sql` | DB 마이그레이션 SQL (Railway에 적용 완료) |
| `docs/ARCHITECTURE_CURRENT.md` | 현재 아키텍처 설명 (Layer 2 반영) |
| `plans/ROADMAP.md` | 프로젝트 로드맵 |

---

## 완료된 작업 (2026-03-15)

### DB 스키마
- [x] Railway DB에 `schema_layer2.sql` 실행 완료 (6개 신규 테이블 + reports 컬럼 3개)
- [x] `db/models.py`에 ORM 클래스 추가 (ReportMarkdown, ReportAnalysis, ReportStockMention, ReportSectorMention, ReportKeyword, AnalysisJob)

### 신규 모듈
- [x] `parser/markdown_converter.py` — PDF → Markdown 변환 (PyMuPDF4LLM + pypdf fallback)
- [x] `parser/layer2_extractor.py` — Layer 2 구조화 추출 (Sonnet 1회 호출, tool_use)
- [x] `storage/analysis_repo.py` — 분석 결과 트랜잭션 저장 (DELETE+INSERT 패턴)
- [x] `scripts/run_analysis.py` — 배치 분석 CLI (--limit, --dry-run, --reprocess)

### 파이프라인 재구성
- [x] `collector/listener.py` — S2b/Stage5 제거, Layer2 파이프라인으로 교체
- [x] `collector/backfill.py` — 동일하게 Layer2 파이프라인 적용
- [x] `_apply_layer2_meta()` — Layer2 meta → reports 테이블 필드 업데이트

### 웹/API
- [x] `api/routers/stats.py` — analysis_status 집계, 카테고리 분포 API
- [x] `web/app/stats/page.tsx` — Layer 2 분석 현황 섹션 (완료/대기/실패, 카테고리 바 차트)
- [x] `web/lib/api.ts` — OverviewStats 인터페이스 확장

### 테스트
- [x] `tests/test_layer2.py` — 16개 테스트 (extractor, dataclass, meta, converter, dedup)
- [x] `tests/test_collector.py` — 새 파이프라인 기준 mock 업데이트

### 실제 테스트 결과
- 20건 Layer2 분석 실행: **19/20 성공** (1건 네트워크 타임아웃)
- 카테고리 분포: stock(2), industry(10), macro(7)
- 연관 데이터: 42 stock_mentions, 56 sector_mentions, 222 keywords
- LLM 비용: 27건 × ~$0.034 ≈ **$0.91** (Sonnet)

---

## 남은 작업

### 1. 기존 리포트 전체 Layer2 백필 (우선순위: 높음)

현재 ~1045건 `analysis_status = 'pending'`.

```bash
# 10건씩 배치 실행
PYTHONUTF8=1 .venv/Scripts/python -m scripts.run_analysis --limit 50

# 전체 실행 (약 $30-50 예상)
PYTHONUTF8=1 .venv/Scripts/python -m scripts.run_analysis
```

주의: Railway DB 연결이 간헐적으로 타임아웃될 수 있음. `--limit`으로 나눠 실행 권장.

### 2. 배포 (우선순위: 높음)

Railway/Vercel에 현재 변경분 반영 필요:
- **Railway**: `parser/`, `storage/analysis_repo.py`, `collector/`, `config/settings.py`, `db/models.py` 변경
- **Vercel**: `web/app/stats/page.tsx`, `web/lib/api.ts` 변경
- `requirements.txt`에 `pymupdf4llm>=0.0.17` 추가됨 → Railway 빌드 시 자동 설치

### 3. 레거시 코드 정리 (우선순위: 중간)

Layer2로 통합되어 미사용 상태인 코드:
- `parser/llm_parser.py`의 `extract_metadata()` (S2b) — 함수 자체 제거 또는 deprecated 표시
- `parser/pdf_analyzer.py` (Stage5) — 파일 삭제 가능
- `scripts/analyze_pdfs.py` — Layer2로 대체됨, 삭제 가능
- `scripts/reparse_llm.py` — S2b 재파싱용, 삭제 가능
- `reports` 테이블의 `ai_summary`, `ai_sentiment`, `ai_keywords` 컬럼 — 기존 데이터 보존, 신규 미사용

### 4. 리포트 상세 페이지 Layer2 표시 (우선순위: 중간)

현재 `web/app/reports/[id]/page.tsx`에는 기존 ai_summary/keywords만 표시.
Layer2 데이터를 보여주려면:
- API: `/api/reports/{id}`에 analysis_data, stock_mentions, sector_mentions, keywords 포함
- 웹: 투자 논리 체인 시각화, 연관 종목/섹터 표시

### 5. NAS 리스너 설정 (우선순위: 낮음)

- NAS(DS216play)에 Python + 의존성 설치
- Telegram 리스너 상시 실행 (systemd 또는 screen)
- `pymupdf4llm` NAS에서 동작 확인 필요 (ARM 아키텍처)

---

## 주요 파일 맵

| 파일 | 역할 | 상태 |
|------|------|------|
| `parser/layer2_extractor.py` | Layer2 구조화 추출 | **신규** |
| `parser/markdown_converter.py` | PDF → Markdown | **신규** |
| `storage/analysis_repo.py` | 분석 결과 저장 | **신규** |
| `scripts/run_analysis.py` | 배치 분석 CLI | **신규** |
| `collector/listener.py` | 실시간 수집 파이프라인 | 재구성 |
| `collector/backfill.py` | 히스토리 백필 | 재구성 |
| `db/models.py` | ORM 모델 | 클래스 추가 |
| `config/settings.py` | 설정 | 필드 추가 |
| `api/routers/stats.py` | 통계 API | Layer2 집계 추가 |
| `web/app/stats/page.tsx` | 통계 대시보드 | Layer2 섹션 추가 |

---

## 설정 (`config/settings.py`)

| 설정 | 기본값 | 설명 |
|------|--------|------|
| `analysis_enabled` | `True` | Layer2 분석 on/off |
| `markdown_converter` | `"pymupdf4llm"` | PDF 변환기 선택 |
| `analysis_schema_version` | `"v1"` | 스키마 버전 (재처리 기준) |
| `analysis_batch_size` | `10` | 배치 크기 |

---

## 알려진 이슈

1. **stock_code 빈 값 처리**: 해외 종목은 stock_code가 비어있음. `company_name[:20]`을 대리 키로 사용하여 UNIQUE 제약 우회. 향후 해외 종목코드 매핑 필요.
2. **Railway 타임아웃**: 대량 배치 시 간헐적 연결 타임아웃 발생. `--limit`으로 분할 실행.
3. **test_storage 실패**: 기존 DB 상태 의존 테스트 3개 실패 (pre-existing, Layer2와 무관).
