# Windows 머신 세팅 가이드

다른 Windows 머신으로 report-collector를 옮길 때 따라할 0→100 가이드.
스케줄러 등록은 [`scheduler_setup.md`](scheduler_setup.md) 참조.

---

## 0. 사전 요구사항

| 항목 | 버전 / 비고 |
|---|---|
| Windows | 10 / 11 |
| Python | **3.12** (`runtime.txt` 기준) |
| Node.js | LTS (Claude Code CLI용) |
| Git | for Windows |
| PostgreSQL | Railway 원격 사용 (로컬 설치 불필요) |
| 디스크 | PDF 저장용 별도 드라이브 권장 (현재 `F:\report-collector\pdfs\`) |

설치 확인:
```cmd
python --version          REM Python 3.12.x
node --version            REM v20+
git --version
```

---

## 1. 레포 클론 + Python venv

```cmd
cd C:\Users\<USER>\Projects
git clone <repo-url> report-collector
cd report-collector

python -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
```

## 2. 의존성 설치

```cmd
pip install -r requirements.txt

REM pykrx는 numpy<2.0 제약 충돌로 requirements.txt에서 제외됨
REM 별도 --no-deps 설치
pip install --no-deps pykrx==1.0.51
```

> `pykrx` 버전: `1.2.4`는 PyPI에 없음 → `1.0.51` 사용 (커밋 `d099bb0` 참조)

설치 확인:
```cmd
python -c "import telethon, sqlalchemy, anthropic, google.genai, pykrx; print('ok')"
```

---

## 3. `.env` 설정

`config/.env` 생성 (UTF-8, BOM 없음):

```env
# Telegram (리스너 전용)
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=abcdef1234567890abcdef1234567890
TELEGRAM_SESSION_NAME=report_collector
TELEGRAM_CHANNELS=@meritz_research,@DSInvResearch,@sunstudy1004,@companyreport,@HanaResearch,@report_figure_by_offset

# DB (Railway PostgreSQL)
DATABASE_URL=postgresql://user:pass@host:port/dbname

# LLM
ANTHROPIC_API_KEY=sk-ant-...
GEMINI_API_KEY=AIza...

# PDF 저장
PDF_BASE_PATH=F:\report-collector\pdfs

# 분석 옵션
ANALYSIS_ENABLED=true
LLM_MODEL=claude-haiku-4-5-20251001
LLM_PDF_MODEL=claude-sonnet-4-6
GEMINI_MODEL=gemini-2.5-flash-lite
```

> **주의**: `config/.env`가 위치. 프로젝트 루트가 아님 (`config/settings.py:9` 참조)

키 수집처:
- `TELEGRAM_API_ID`/`HASH`: https://my.telegram.org/apps
- `DATABASE_URL`: Railway 프로젝트 → Variables
- `ANTHROPIC_API_KEY`: https://console.anthropic.com/
- `GEMINI_API_KEY`: https://aistudio.google.com/app/apikey

---

## 4. PDF 저장 디렉토리 생성

```cmd
mkdir F:\report-collector\pdfs
```

> 다른 드라이브 쓰면 `.env`의 `PDF_BASE_PATH` 변경.
> 백필 누적치는 수십 GB까지 갈 수 있음 → 별도 디스크 권장.

---

## 5. DB 마이그레이션

```cmd
.venv\Scripts\activate
alembic -c db\migrations\alembic.ini upgrade head
```

확인:
```cmd
python -c "from db.session import get_sync_session; from sqlalchemy import text; s=next(get_sync_session()); print(s.execute(text('SELECT COUNT(*) FROM reports')).scalar())"
```

> Railway 공유 DB를 그대로 쓴다면 마이그레이션은 이미 적용된 상태 — `upgrade head`는 no-op.

---

## 6. Telegram 세션

두 가지 옵션. 둘 다 동일 API_ID로 가능 (계정 단위로 세션 분리됨).

### 옵션 A: 기존 머신 세션 복사 (가장 빠름)

기존 머신의 `report_collector.session` 파일을 복사:
```cmd
REM 기존 머신에서
copy C:\Users\praus\Projects\report-collector\report_collector.session \\share\

REM 새 머신에서
copy \\share\report_collector.session C:\Users\<USER>\Projects\report-collector\
```

> ⚠️ **주의**: 같은 세션을 두 머신에서 동시에 쓰면 Telegram이 한쪽을 강제 로그아웃시킴.
> 새 머신 가동 직전에 기존 머신 리스너를 끄거나, 옵션 B로 별도 세션을 만드세요.

### 옵션 B: 신규 인증 (별도 세션)

```cmd
.venv\Scripts\activate
python scripts\auth_telegram.py
```
→ 휴대폰 SMS 코드 입력 → `report_collector.session` 자동 생성.

> SMS 안 오면 `auth_step1.py` / `auth_step2.py`를 단계별로 실행.

---

## 7. Claude Code CLI

```cmd
npm install -g @anthropic-ai/claude-code
claude login
```

확인:
```cmd
where claude
REM 결과 예: C:\Users\<USER>\AppData\Roaming\npm\claude.cmd
claude --version
```

> Layer2 OPUS LOW 라운드(`scripts/scheduled_layer2_opus.bat`)가 이걸 호출함.
> `claude_not_found` FAIL이 연발되면 PATH 문제 또는 Claude Code 자동 업데이트 중.

---

## 8. bat 파일 경로 수정

모든 `scripts/*.bat`의 `PROJECT_DIR`을 새 경로로 일괄 수정:

```cmd
REM 대상 파일
scripts\start_listener.bat
scripts\scheduled_layer2.bat
scripts\scheduled_layer2_opus.bat
scripts\scheduled_batch_submit.bat
scripts\scheduled_batch_recover.bat
```

각 파일 상단의:
```bat
set "PROJECT_DIR=C:\Users\praus\Projects\report-collector"
```
→ 새 경로로 변경.

> bat 파일 줄바꿈은 **반드시 CRLF**. Git 설정에서 `core.autocrlf=true`로 자동 변환되도록 두는 게 안전.

---

## 9. 동작 확인 (스케줄러 등록 전)

각 스크립트를 손으로 한 번씩 돌려 로그 확인.

```cmd
REM 1) 리스너 (Ctrl+C로 중단)
scripts\start_listener.bat
type logs\listener.log

REM 2) Layer2 Batch submit (5~10건 소규모로 먼저)
.venv\Scripts\python.exe run_analysis.py --limit 5 --batch-size 5
type logs\scheduled_batch.log

REM 3) Layer2 Claude Code (5건 소규모)
.venv\Scripts\python.exe run_analysis.py --dump-layer2 --dump-layer2-path data\test_dump.jsonl --limit 5
.venv\Scripts\python.exe scripts\claude_layer2.py --input data\test_dump.jsonl --output data\test_out.jsonl --concurrency 1 --timeout 180
.venv\Scripts\python.exe scripts\import_layer2.py --input data\test_out.jsonl --apply

REM 4) Batch recover (in-flight 있을 때만 의미 있음)
.venv\Scripts\python.exe scripts\recover_batches.py --from-pending --apply
```

테스트 통과:
```cmd
.venv\Scripts\activate
pytest tests\ --ignore=tests\test_db_setup.py --ignore=tests\test_storage.py --ignore=tests\test_collector.py -v
```

---

## 10. Task Scheduler 등록

[`scheduler_setup.md`](scheduler_setup.md)의 "Windows 세팅" 섹션 참조.

요약:
| 작업 | 주기 | bat |
|---|---|---|
| 리스너 | 로그온 시 | `start_listener.bat` |
| Batch 제출 | 매일 03시 | `scheduled_batch_submit.bat` |
| Batch 수거 | 매일 04시 | `scheduled_batch_recover.bat` |
| Layer2 Claude Code | 5시간마다 | `scheduled_layer2.bat` |

> Task Scheduler에서 작업 속성 → "사용자가 로그인하지 않아도 실행" 옵션 켤 경우 stored credential 필요.
> 리스너는 사용자 세션이 살아있어야 telethon이 정상 동작 → "로그인할 때만 실행" 권장.

---

## 11. 머신 이전 체크리스트

- [ ] Python 3.12 설치
- [ ] Node.js LTS + Claude Code CLI 설치 + `claude login`
- [ ] 레포 클론 + venv + 의존성 (pykrx 별도)
- [ ] `config/.env` 작성 (5개 키 + DB URL + PDF 경로)
- [ ] PDF 저장 디렉토리 생성
- [ ] DB 마이그레이션 (Railway 공유면 no-op)
- [ ] Telegram 세션 복사 또는 재인증 (**중복 가동 주의**)
- [ ] `scripts/*.bat`의 `PROJECT_DIR` 일괄 수정
- [ ] 각 스크립트 수동 1회 실행 + 로그 확인
- [ ] pytest 통과
- [ ] Task Scheduler 등록
- [ ] (기존 머신) 스케줄 작업 비활성화 + 리스너 종료

---

## 12. 트러블슈팅

### `claude_not_found` 연발
- `where claude` 로 경로 확인. PATH에 `%APPDATA%\npm` 들어있는지 확인.
- Claude Code 자동 업데이트 중일 수 있음 → 잠시 후 재시도.
- npm global 권한 문제면 `npm config get prefix` 후 권한 확인.

### `psycopg2.errors.OperationalError: connection refused`
- `DATABASE_URL` 형식 확인 (Railway는 `postgresql://`로 시작).
- Railway 프로젝트의 DB가 sleep 모드일 수 있음 → 대시보드에서 깨우기.

### Telegram `AuthKeyDuplicatedError`
- 같은 세션을 두 머신에서 동시 사용 → 한쪽 종료 후 재인증.

### 한글 파일명 깨짐 (`logs/listener.log`에 ????`)
- bat 파일 첫 줄에 `chcp 65001 >nul` 있어야 (이미 다 들어가 있음).
- PDF 경로 자체에 한글 들어가지 않게 영문 경로로 유지.

### `MemoryError` in `run_analysis.py` (`tracemalloc.take_snapshot`)
- 04-17일 사례. `_dump_memory_snapshot` 호출이 메모리 압박 — 디버깅용 코드라 운영에서는 비활성화 권장.
- 작업자 수(`--concurrency`)를 4 이하로 유지.

### Batch recover access violation (`exit code 1073807364`)
- 04-23일 사례 (0xC0000005). 재실행하면 보통 회수 완료됨.
- 자주 반복되면 psycopg2 / SQLAlchemy 버전 점검.

---

## 13. 비밀 파일/디렉토리 (절대 git commit 금지)

| 파일 | 용도 |
|---|---|
| `config/.env` | 모든 시크릿 |
| `report_collector.session` | Telegram 세션 (계정 토큰) |
| `~/.claude/` | Claude Code 인증 토큰 |

`.gitignore` 확인:
```cmd
git check-ignore config\.env report_collector.session
```
→ 두 파일 모두 출력되어야 함.
