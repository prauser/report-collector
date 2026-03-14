# Report Collector 개발 로드맵

## 인프라 구성

```
NAS (DS216play)   → Telegram 리스너 + PDF 아카이빙 (24/7)
Railway           → FastAPI + PostgreSQL
Vercel            → Next.js 웹
```

---

## 완료

- [x] Telegram 채널 수집기 (listener + backfill)
- [x] 1단계 파싱: regex 기반 메시지 분류
- [x] 2단계 파싱: Claude Haiku LLM 분류/추출
- [x] PDF 다운로드 및 저장
- [x] PDF 본문 AI 분석 (Claude Sonnet — 요약/감성/키워드)
- [x] LLM 비용 추적 (llm_usage 테이블, message_type 필터링 지표)
- [x] FastAPI 백엔드 (리포트 검색/필터/상세, 통계)
- [x] Next.js 웹 (검색 목록, 상세 페이지, LLM 비용 대시보드)

### Phase 1 — 배포 (완료)
- [x] Railway에 FastAPI 서비스 추가
- [x] Vercel에 Next.js 배포
- [x] 채널 관리 웹 UI (추가/활성화/삭제)
- [x] Pending 메시지 검토 UI
- [x] 백필 관리 웹 페이지 (채널별 상태, PDF 커버리지, parse_quality 분포)

### Phase 2 — Layer 2 분석 시스템 (완료, 2026-03-15)
- [x] 3-Layer 데이터 아키텍처 설계 (`docs/LAYER2_DESIGN.md`)
- [x] DB 스키마 확장 (6개 신규 테이블 + reports 컬럼 추가)
- [x] S2b + Stage5 → 단일 Layer2 추출로 통합
- [x] PDF → Markdown 변환 (`parser/markdown_converter.py`, PyMuPDF4LLM)
- [x] Layer2 구조화 추출 (`parser/layer2_extractor.py`, Sonnet 1회 호출)
- [x] 투자 논리 체인 스키마 (stock/industry/macro 카테고리별)
- [x] 분석 결과 트랜잭션 저장 (`storage/analysis_repo.py`)
- [x] collector/listener.py, backfill.py 파이프라인 재구성
- [x] 배치 분석 스크립트 (`scripts/run_analysis.py`)
- [x] 웹 통계에 Layer 2 분석 현황 추가
- [x] 20건 실제 리포트 테스트 완료 (19/20 성공)

---

## 진행 예정

### Phase 2.5 — Layer 2 안정화
- [ ] 기존 리포트 전체 Layer2 백필 (~1045건 pending)
- [ ] S2b (`extract_metadata`) / Stage5 (`pdf_analyzer.py`) 코드 정리/삭제
- [ ] 리포트 상세 페이지에 Layer2 분석 결과 표시
- [ ] Railway/Vercel 재배포 (Layer2 변경분 반영)
- [ ] NAS 리스너 상시 실행 설정

### Phase 3 — 웹 고도화
- [ ] 종목별 리포트 히스토리 페이지
- [ ] 투자 논리 체인 시각화
- [ ] 종목/섹터 크로스 분석 뷰
- [ ] 실시간 업데이트 (SSE 또는 polling)
- [ ] 모바일 UX 개선

### Phase 4 — AI Agent (목표)
- [ ] 크로스 리포트 종합 분석 Agent
- [ ] 자연어 질의 인터페이스
- [ ] 종목별 투자의견 변화 추적
- [ ] 산업 트렌드 분석

### Phase 5 — 앱 (선택)
- [ ] Expo React Native 앱 (웹 API 재사용)
- [ ] 푸시 알림 (신규 리포트)

---

## 환경변수 체크리스트

### Railway
| 변수 | 설명 |
|---|---|
| `DATABASE_URL` | PostgreSQL 연결 (자동 링크) |
| `ANTHROPIC_API_KEY` | Claude API 키 |
| `ALLOWED_ORIGINS` | Vercel 도메인 (배포 후 추가) |

### Vercel
| 변수 | 설명 |
|---|---|
| `NEXT_PUBLIC_API_URL` | Railway API URL |

### NAS
| 변수 | 설명 |
|---|---|
| `DATABASE_URL` | Railway PostgreSQL 외부 연결 URL |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | Telegram 앱 키 |
| `ANTHROPIC_API_KEY` | Claude API 키 |
