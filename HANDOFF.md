# HANDOFF — 다음 세션 시작점 (2026-04-29)

## 이번 세션 완료 사항

### 1. `parser/image_extractor.py` — 다중 시그널 필터링 도입
- 단일 텍스트 커버리지 임계값 → **다중 시그널 스코어링** 교체
- 시그널: 벡터 밀도(+2), 큰 임베드 이미지(+2), 키워드(+3), 섹션 헤더 키워드(+2), 인접 페이지(+1)
- **threshold ≥ 3** 페이지만 chart_digitize 호출
- 표지/면책 hard skip (total > 3페이지 시), 3페이지 이하는 면제
- dry-run 30건 검증: 통과율 51%, 리포트당 평균 6.3장

### 2. `run_analysis.py:66` — chart_digitize 화이트리스트 확대
```python
# Before
_QUANT_REPORT_TYPES = {"퀀트"}
# After
_CHART_DIGITIZE_TYPES = {"퀀트", "기업분석", "실적리뷰", "산업분석"}
```

### 3. `scripts/dryrun_image_filter.py` 신규
- 실제 API 호출 없이 페이지별 시그널/점수 dry-run 확인용
- `--diverse --sample 30` 옵션으로 broker×report_type 다양성 샘플링

### 4. chart_digitize 산출물 DB 영속화 (Task A 완료)
- `db/migrations/versions/h2c3d4e5f6a7_add_report_chart_text.py` — alembic migration 작성
- `storage/chart_text_repo.py` — `load_chart_text()` / `save_chart_text()` 구현
- `parser/chart_digitizer.py` — `get_or_digitize_charts()` 캐시 패턴 추가
- `run_analysis.py` — chart_digitize 호출부를 `get_or_digitize_charts`로 교체
- Railway DB 마이그레이션 적용 완료 (`alembic current`: h2c3d4e5f6a7)

---

## 다음 세션 구현 목표

### A (최우선): 기존 24,895건 chart_digitize 백필
- `scripts/run_chart_prefetch.py` 신규 작성
- `pipeline_status = 'done'` + chart_text 없는 리포트 대상
- `_CHART_DIGITIZE_TYPES`에 해당하는 report_type만
- concurrency 조절 (Gemini semaphore 5 유지)
- 예상 비용: ~$52 (7만원), 시간: 수 시간

---

## 아키텍처 결정사항 (확정)

| 항목 | 결정 |
|---|---|
| chart pipeline 위치 | listener/backfill 변경 없음, analysis 내 캐시 패턴 |
| pipeline status 변경 | 없음 (chart_done 단계 추가 불필요) |
| Layer2 재추출 방식 | DB에서 chart_text 읽어서 기존 flow 유지 |
| 백필 방식 | 별도 `run_chart_prefetch.py` 1회 실행 |
| opendataloader-pdf | 기각 (base 표 구조 품질 실측 미달, 한국 리포트 특화 미검증) |
| 로컬 VLM | 기각 (내장 GPU 0.5GB, 7B+ 모델 필요) |
| Gemini Flash-Lite | 유지 (`config/settings.py:60`, `.env:33`) |

---

## Layer2 v2 스키마 (보류 — A/B 완료 후)

다음 단계로 예정되어 있으나 이번 세션에서 구현 안 함:
```json
{
  "valuation": {"method":"PBR","applied_multiple":0.31,"base_metric":"2026F BPS","target_year":"2026F","justification_text":"..."},
  "catalysts": [{"event":"1Q26 어닝콜","expected_date":"2026-05-15","importance":"high"}],
  "monitoring_points": [{"indicator":"철근 유통가격","source":"한국철강협회","direction_for_thesis":"up"}]
}
```
신규 리포트부터 적용, 기존 24,895건 재추출은 정형 쿼리 수요 확인 후 결정.

---

## 현재 비용 구조 (참고)

| 단계 | 모델 | 단가/리포트 |
|---|---|---|
| key_data | Gemini Flash-Lite | ~$0.001 |
| chart_digitize | Gemini Flash-Lite | ~$0.002 (avg 6.3장) |
| Layer2 | Sonnet Batch | ~$0.052 |
| **합계** | | **~$0.055** |

백필 24,895건 chart_digitize: **~$52 (7만원)**
운영 일 100건: **~$6/월**

---

## 관련 파일 위치

| 파일 | 역할 |
|---|---|
| `parser/image_extractor.py` | 다중 시그널 필터링 (이번 세션 변경) |
| `parser/chart_digitizer.py` | Gemini chart_digitize 로직 |
| `run_analysis.py:66` | `_CHART_DIGITIZE_TYPES` 화이트리스트 |
| `scripts/dryrun_image_filter.py` | 필터 dry-run 도구 |
| `db/models.py` | DB 모델 (report_chart_text 추가 필요) |
| `storage/llm_usage_repo.py` | LLM 비용 기록 패턴 참고용 |
