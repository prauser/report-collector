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

---

## 진행 예정

### Phase 1 — 배포 (지금)
- [ ] Railway에 FastAPI 서비스 추가
- [ ] Vercel에 Next.js 배포
- [ ] NAS에 Entware + Python 설치
- [ ] NAS에서 Telegram 리스너 상시 실행 설정

### Phase 2 — 데이터 품질
- [ ] 기존 데이터 LLM 재파싱 (`scripts/reparse_llm.py`)
- [ ] 기존 PDF AI 분석 배치 실행 (`scripts/analyze_pdfs.py`)
- [ ] KRX 전체 종목코드 로드 (현재 40개 시드)
- [ ] @searfin / @cb_eq_research 전용 파서 개발

### Phase 3 — 웹 고도화
- [ ] 종목별 리포트 히스토리 페이지
- [ ] 감성 점수 트렌드 차트
- [ ] 실시간 업데이트 (SSE 또는 polling)
- [ ] 모바일 UX 개선

### Phase 4 — 앱 (선택)
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
