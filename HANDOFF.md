# HANDOFF — 다음 세션 시작점 (2026-05-04)

## 이번 세션 완료 사항

### 1. 스케줄 재구성 (Layer2 + Batch)
- Layer2_Claude_Codex_Split / _Opus: 5h drift → **6h 고정 (00/06/12/18)**
- Layer2_Batch_Recover: 09:00 → 09:30 (run_analysis 06:00 cycle 끝난 후)
- Layer2_Batch_Submit: 04:30 유지 (00:00 cycle 끝난 후 안전 슬롯)
- 5월 5일(화) 23:00 자동 Sonnet 복귀 1회성 task `Layer2_Switch_To_Sonnet_Once` 등록

### 2. Sonnet → Opus Low 스위치 완료
- Layer2_Claude_Codex_Split (Sonnet) Disabled, _Opus Enabled
- Sonnet 주간 쿼터 소진 → 5-05 23:00 자동 복귀

### 3. markdown_converter timeout 상향 (commit `ceb6c42`)
- 120s → 300s
- 5-01 markdown_failure 156건 burst 분석 결과: workers=4 동시 실행 시 50~70p PDF가 120s 초과해 실패 → 300s 충분

### 4. chart_digitize 비활성화 — 효용 미입증으로 폐기 (commit `984e278`)
**측정**: chart_only grounding 0.79% (200건 샘플), valuation_impact step markdown grounding 94.4% (300건). chart_digitize는 Layer2 valuation/financial 근거에 거의 기여 안 함.
- `run_analysis.py`: `_CHART_DIGITIZE_TYPES = set()` (빈 화이트리스트)
- `_should_digitize=False`면 image extraction도 건너뜀 (CPU 절약)
- 24,895건 백필 ($52) 계획 **폐기**
- `report_chart_text` 테이블/repo/캐시 코드는 유지 (재활성화 1줄 변경)
- 측정 스크립트 3개 신규: `measure_chart_grounding.py`, `inspect_valuation_grounding.py`, `inspect_full_reasoning.py`

---

## 다음 세션 구현 목표

### A: markdown 실패 PDF 직렬 재처리 (~400건 회복)
- `pipeline_status='analysis_failed'` 중 reason=`no_markdown` 리포트
- `workers=1`로 단독 직렬 실행 (CPU 경쟁 없음)
- `_MD_CONVERT_TIMEOUT=300` 효과로 대부분 회복 예상

### B (선택): markdown_converter 별도 worker queue 분리 (장기)
- pymupdf4llm CPU 경쟁 해소
- 현재 60s timeout pypdf fallback은 같은 PDF엔 의미 없음 → 제거 검토

---

## 아키텍처 결정사항 (확정)

| 항목 | 결정 |
|---|---|
| chart_digitize | **비활성화** (2026-05-04) — 효용 미입증, 측정 결과 0.79% grounding |
| chart 코드 보존 | parser/chart_digitizer.py, image_extractor.py 등 유지 — 재활성화 시 1줄 변경 |
| report_chart_text 테이블 | 유지 (이미 누적된 데이터 폐기 X) |
| run_analysis 5h → 6h | drift 제거, Claude quota 5h 리셋과 1h 마진 |
| Sonnet/Opus 자동 스위치 | Windows Task Scheduler 1회성 task로 처리 |

---

## Layer2 v2 스키마 (재검토 필요)

```json
{
  "valuation": {"method":"PBR","applied_multiple":0.31,"base_metric":"2026F BPS","target_year":"2026F","justification_text":"..."},
  "catalysts": [{"event":"1Q26 어닝콜","expected_date":"2026-05-15","importance":"high"}],
  "monitoring_points": [{"indicator":"철근 유통가격","source":"한국철강협회","direction_for_thesis":"up"}]
}
```
chart_digitize 폐기로 v2 도입 시 chart 의존 필드 제외 필요.

---

## 현재 비용 구조 (chart_digitize 제거 후)

| 단계 | 모델 | 단가/리포트 |
|---|---|---|
| key_data | Gemini Flash-Lite | ~$0.001 |
| ~~chart_digitize~~ | ~~Gemini Flash-Lite~~ | ~~$0.002~~ → **$0** |
| Layer2 | Sonnet/Opus Batch | ~$0.052 |
| **합계** | | **~$0.053** |

운영 일 100건: $5.3/월 (이전 $6/월에서 ~10% 절감)

---

## 운영 검증 도구 (신규 스크립트)

| 스크립트 | 용도 |
|---|---|
| `scripts/measure_chart_grounding.py` | chart vs markdown 숫자 grounding 측정 |
| `scripts/inspect_valuation_grounding.py` | valuation chain step grounding 검증 |
| `scripts/inspect_full_reasoning.py` | 리포트별 chain/financials 깊이 확인 |

읽기 전용, DB만 사용. 향후 Layer2 출력 품질 회귀 모니터링에 활용.

---

## 관련 파일 위치

| 파일 | 역할 |
|---|---|
| `run_analysis.py:67` | `_CHART_DIGITIZE_TYPES` (현재 빈 set) |
| `parser/markdown_converter.py:62` | `_MD_CONVERT_TIMEOUT = 300` |
| `parser/chart_digitizer.py` | chart 추출 (현재 미사용, 코드 보존) |
| `storage/chart_text_repo.py` | report_chart_text 저장소 (현재 미사용) |
| `db/models.py: ReportChartText` | DB 모델 (재활성화 시 즉시 사용 가능) |
