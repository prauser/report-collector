# 핸드오프 — Pipeline 안정화 + Analysis 병렬화

> 작성: 2026-03-26
> 이전 핸드오프: 2026-03-15 (Layer 2 구현 완료)

---

## 이번 세션 완료 작업

### 환경 세팅 (WSL)
- Windows `.env` → WSL `config/.env` 복사 (DATABASE_URL, API 키 등)
- Telegram 세션 파일 복사 (`report_collector.session`)
- `PDF_BASE_PATH`를 `/mnt/f/report-collector/pdfs`로 설정
- DB `pdf_path` 백슬래시→슬래시 일괄 변환 (8,891건)

### Pipeline Hang 버그 수정 (4건)
| 파일 | 수정 내용 |
|------|----------|
| `parser/chart_digitizer.py` | Semaphore 데드락 수정 — backoff를 semaphore 밖으로 이동, genai.Client 캐싱, timeout 60→90초, 503 재시도 추가, 매직넘버 상수화 |
| `storage/pdf_archiver.py` | `download_media()`에 `wait_for(timeout=120)` 래핑 |
| `collector/telegram_client.py` | `connection_retries=3`, `timeout=30`, `request_retries=3` 추가 |
| `collector/backfill.py` | worker gather에 전체 timeout safety net, gemini_api_key 없을 때 이미지 추출 스킵 |

### Analysis Phase 1 병렬화
- `run_analysis.py`: `asyncio.gather` → Queue/Worker 패턴으로 변경
- `--concurrency` CLI 옵션 추가 (기본값 4)
- progress 카운터가 성공+실패+timeout 전부 카운트

### 데이터 처리 실적
| 작업 | 건수 |
|------|------|
| pdf_failed 재시도 (retryable) | 587건 → 3건 성공 |
| backfill 실행 | 3,759건 수집 |
| analysis 실행 | ~270건 완료 (50건 + 500건 배치 중 일부) |

### 커밋
| 해시 | 내용 |
|------|------|
| `fb92238` | fix: pipeline hang 방지 + analysis Phase 1 병렬화 |
| `ca4dd88` | refactor: simplify 리뷰 반영 — 품질/효율 개선 |

---

## 현재 파이프라인 상태

```
pipeline_status       건수
─────────────────────────
s2a_done             12,773    PDF 다운로드 대기 (pdf_url 없음, Telegram 첨부파일)
pdf_failed           12,204    대부분 복구 불가 (서버 차단/서비스 폐쇄)
analysis_pending      8,006    Layer2 분석 대기 (PDF 있음)
new                   2,282    S2a 분류 안 된 초기 데이터
done                  1,116    완료
pdf_done                197    분석 대기 (run_analysis가 자동 포함)
─────────────────────────
합계                 36,578
```

---

## 즉시 실행할 운영 작업

### 1. analysis_pending 전체 처리 (Windows에서)
```bash
cd C:\Users\praus\Projects\report-collector
git pull
python run_analysis.py --concurrency 8
```
- 대상: ~8,000건
- Windows 로컬 PDF I/O라 WSL 대비 5-10x 빠름
- Gemini 503 간헐 발생하지만 재시도 로직 적용됨

### 2. s2a_done Telegram 첨부파일 다운로드
```bash
python run_backfill.py
```
- 12,773건 중 대부분 @sunstudy1004 (11,927건)
- `pdf_url=None`이라 Telegram 첨부파일로만 다운로드 가능

### 3. pdf_failed 정리
- 12,204건 중 retryable 이미 시도 완료 (587건 → 3건 성공)
- 나머지는 `retryable=False`로 일괄 전환 권장

---

## 미구현 기능 작업

### Phase 2.5 — Layer 2 안정화
- [ ] S2b (`extract_metadata`) / Stage5 (`pdf_analyzer.py`) 레거시 코드 삭제
- [ ] NAS 리스너 상시 실행 설정

### Phase 3 — 웹 고도화 (미착수)
- [ ] 종목별 리포트 히스토리 페이지
- [ ] 투자 논리 체인 시각화
- [ ] 종목/섹터 크로스 분석 뷰
- [ ] 실시간 업데이트 (SSE 또는 polling)
- [ ] 모바일 UX 개선

### Phase 4 — AI Agent 고도화
- 기본 구조 완료 (`api/routers/agent.py`, `web/app/agent/page.tsx`)
- [ ] 크로스 리포트 종합 분석
- [ ] 종목별 투자의견 변화 추적
- [ ] 산업 트렌드 분석

### 매매 저널 (webapp-expansion-plan.md)
- [ ] CSV 업로드 → 체결 기록
- [ ] 캔들차트 + 마커 + 보조지표
- [ ] 성과 분석 대시보드
- [ ] 복기 페이지

---

## 배포 상태

- **Railway**: `railway.toml` 설정됨, GitHub push 시 자동 배포
- **Vercel**: `vercel.json` 설정됨, GitHub push 시 자동 배포
- 2026-03-26 push 완료 → 자동 배포 트리거됨 (대시보드에서 확인 필요)
- 배포 시 Layer2 API, AI Agent 챗봇, 매매 저널 기능이 웹에서 활성화됨

---

## 알려진 이슈

1. **WSL `/mnt/f/` I/O 느림**: PDF 분석 시 건당 70초. Windows 로컬에서 실행 권장
2. **Gemini 503**: 서버 과부하 시 간헐 발생. 10초 backoff 후 1회 재시도 적용됨
3. **Telegram 세션**: WSL에서 복사한 세션이 DC 인증 불일치 가능. Windows 원본 사용 권장
4. **pdf_failed 12,204건**: 대부분 `not_pdf:html_response`(8,083건) + 서버 차단. 복구 불가

---

## 참조 문서

| 파일 | 내용 |
|------|------|
| `docs/LAYER2_DESIGN.md` | Layer 2 설계 (3-Layer 아키텍처, 체인 스키마) |
| `docs/ARCHITECTURE_CURRENT.md` | 현재 아키텍처 |
| `docs/webapp-expansion-plan.md` | 매매 저널 + AI Agent 확장 플랜 |
| `plans/ROADMAP.md` | 프로젝트 로드맵 |
