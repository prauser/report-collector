# Layer2 분석 — Claude Code CLI 활용 계획

## 배경
- Layer2 배치(Sonnet API)가 전체 LLM 비용의 97% ($891 / 14,672건, 건당 ~$0.065)
- Claude Code Max 플랜 구독 중 → CLI `claude -p` 활용하면 추가 API 비용 없음
- 미처리 ~75,000건, API로 하면 ~$4,875 예상

## 아키텍처

```
[1단계: 기존 파이프라인]          [2단계: Claude Code]         [3단계: DB 저장]
run_analysis.py                  claude_layer2.py             import_layer2.py
 ├─ key_data (Gemini)             ├─ 입력 JSONL 읽기           ├─ 출력 JSONL 읽기
 ├─ markdown (pymupdf)            ├─ claude -p 호출            ├─ make_layer2_result()
 ├─ charts (Gemini)               ├─ JSON 파싱                 ├─ save_analysis()
 └─ user_content 덤프 →           └─ 출력 JSONL 저장 →         └─ pipeline_status→done
    data/layer2_inputs.jsonl         data/layer2_outputs.jsonl
```

## 1단계: Layer2 입력 덤프 모드 추가

`run_analysis.py`에 `--dump-layer2` 옵션 추가. 배치 제출 대신 JSONL 파일로 덤프.

```python
# data/layer2_inputs.jsonl (한 줄에 한 건)
{
  "report_id": 12345,
  "user_content": "## 텔레그램 원문\n...\n## PDF 마크다운\n...",
  "md_truncated": false,
  "md_chars": 15000,
  "channel": "@companyreport"
}
```

### 구현 포인트
- `run_analysis.py`의 `_flush_buffer()` 대신 JSONL append
- `--dump-layer2` 플래그 시 Anthropic API key 불필요
- pending_batches.jsonl에는 기록하지 않음 (API 미사용)

## 2단계: Claude Code CLI로 Layer2 처리 (scripts/claude_layer2.py)

### 핵심 로직
```python
import subprocess, json

SYSTEM_PROMPT = """...(layer2_extractor.py의 _SYSTEM_PROMPT 그대로)..."""

TOOL_SCHEMA = """...(layer2_extractor.py의 _EXTRACT_TOOL input_schema 그대로)..."""

OUTPUT_INSTRUCTION = """
위 리포트를 분석하여 extract_layer2 스키마에 맞는 JSON을 반환하세요.
반드시 아래 형식으로만 응답하세요:
```json
{ ... }
```
"""

def process_one(report_id: int, user_content: str) -> dict | None:
    prompt = f"{SYSTEM_PROMPT}\n\n## 출력 스키마\n{TOOL_SCHEMA}\n\n{OUTPUT_INSTRUCTION}\n\n---\n\n{user_content}"
    
    result = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "json"],
        capture_output=True, text=True, timeout=120
    )
    
    if result.returncode != 0:
        return None
    
    # JSON 파싱
    return json.loads(result.stdout)
```

### 고려사항
- **`claude -p`의 `--output-format json`**: 응답을 JSON으로 받을 수 있는지 확인 필요
- **프롬프트 구성**: tool_use 대신 system prompt에 스키마를 넣고 JSON 응답 강제
- **타임아웃**: 건당 120초, 실패 시 skip + 재시도 큐
- **동시성**: subprocess 여러 개 병렬 (concurrent.futures)
- **Max 플랜 rate limit**: 분당 요청 수 제한 확인 필요, 초과 시 sleep

### 실행
```bash
python scripts/claude_layer2.py --input data/layer2_inputs.jsonl --output data/layer2_outputs.jsonl --concurrency 3
```

## 3단계: 결과 DB 저장 (scripts/import_layer2.py)

### 핵심 로직
```python
# data/layer2_outputs.jsonl 읽기
# 각 줄: {"report_id": 12345, "result": {...}, "input_tokens": 0, "output_tokens": 0}

# 기존 함수 재사용
from parser.layer2_extractor import make_layer2_result
from storage.analysis_repo import save_analysis
from parser.meta_updater import apply_layer2_meta
```

### 구현 포인트
- `make_layer2_result(tool_input, ...)` 재사용 — tool_input이 JSON dict
- input/output tokens는 CLI에서 알 수 없으므로 0 또는 추정값
- cost_usd = 0 (Max 플랜이므로)
- pipeline_status → "done" 전이

### 실행
```bash
python scripts/import_layer2.py --input data/layer2_outputs.jsonl --apply
```

## 운영 플로우

```bash
# 1. Layer2 입력 준비 (Gemini 비용만 발생, ~$0.0003/건)
python run_analysis.py --dump-layer2

# 2. Claude Code로 처리 (Max 플랜, 추가 비용 없음)
python scripts/claude_layer2.py --input data/layer2_inputs.jsonl --output data/layer2_outputs.jsonl --concurrency 3

# 3. 결과 DB 저장
python scripts/import_layer2.py --input data/layer2_outputs.jsonl --apply

# 4. (선택) 이전에 API로 제출한 배치 결과도 회수
python scripts/recover_batches.py --recover-all --apply
```

## 비용 비교

| 방식 | 75,000건 비용 | 비고 |
|---|---|---|
| Sonnet Batch API | ~$4,875 | 건당 $0.065 |
| Claude Code Max | $0 (구독료만) | rate limit 주의 |
| Gemini 전처리 | ~$22 | key_data + charts |

## 리스크 & 대응

| 리스크 | 대응 |
|---|---|
| Max 플랜 rate limit | concurrency 조절, 429 시 exponential backoff |
| `claude -p` JSON 파싱 실패 | regex fallback으로 ```json``` 블록 추출 |
| 프로세스 중단 | 출력 JSONL append 방식 → 이어서 실행 가능 |
| 품질 차이 (tool_use vs 프롬프트) | 샘플 비교 후 프롬프트 튜닝 |
| 대량 요청 시 이용약관 | Max 플랜 fair use policy 확인 필요 |

## 참조 파일
- `parser/layer2_extractor.py`: _SYSTEM_PROMPT, _EXTRACT_TOOL (tool schema), build_user_content(), make_layer2_result()
- `storage/analysis_repo.py`: save_analysis()
- `parser/meta_updater.py`: apply_layer2_meta()
- `run_analysis.py`: process_single() — 전처리 파이프라인
