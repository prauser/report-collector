# 스케줄러 & 리스너 세팅 가이드

## 사전 준비 (공통)

```bash
# 1. 레포 클론 + venv
git clone <repo-url>
cd report-collector
python -m venv .venv

# 2. 의존성 설치
pip install -r requirements.txt

# 3. 환경변수 설정 (config/settings.py 참조)
#    - DATABASE_URL (PostgreSQL)
#    - ANTHROPIC_API_KEY
#    - GEMINI_API_KEY
#    - TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION
#    - PDF_BASE_PATH (PDF 저장 경로)

# 4. DB 마이그레이션
alembic -c db/migrations/alembic.ini upgrade head

# 5. Claude Code CLI 설치 (claude -p 사용 위해)
npm install -g @anthropic-ai/claude-code
```

---

## 스케줄 작업 목록

| 작업 | 주기 | 스크립트 | 설명 |
|---|---|---|---|
| Layer2 Claude Code | 5시간마다 | `scripts/scheduled_layer2.bat` (.sh) | dump 100건 + claude -p 처리 + DB import |
| Batch 제출 | 매일 03시 | `scripts/scheduled_batch_submit.bat` (.sh) | 300건 Anthropic Batch API 제출 |
| Batch 수거 | 매일 04시 | `scripts/scheduled_batch_recover.bat` (.sh) | 배치 결과 DB 저장 |
| 리스너 | 상시 (로그온 시) | `scripts/start_listener.bat` (.sh) | Telegram 실시간 수집 + PDF |

---

## Windows 세팅

### 스케줄러 등록 (관리자 CMD)

```cmd
REM Layer2 Claude Code (5시간마다)
schtasks /create /tn "Layer2_Claude_Code" /tr "C:\path\to\report-collector\scripts\scheduled_layer2.bat" /sc HOURLY /mo 5 /st 14:00 /f

REM Batch 제출 (매일 03시)
schtasks /create /tn "Layer2_Batch_Submit" /tr "C:\path\to\report-collector\scripts\scheduled_batch_submit.bat" /sc DAILY /st 03:00 /f

REM Batch 수거 (매일 04시)
schtasks /create /tn "Layer2_Batch_Recover" /tr "C:\path\to\report-collector\scripts\scheduled_batch_recover.bat" /sc DAILY /st 04:00 /f

REM 리스너 (로그온 시 자동 실행)
schtasks /create /tn "Report_Listener" /tr "C:\path\to\report-collector\scripts\start_listener.bat" /sc ONLOGON /f
```

### 확인/관리

```cmd
REM 등록 확인
schtasks /query /tn "Layer2_Claude_Code" /v /fo LIST
schtasks /query /tn "Layer2_Batch_Submit" /v /fo LIST

REM 수동 실행
schtasks /run /tn "Layer2_Claude_Code"

REM 삭제
schtasks /delete /tn "Layer2_Claude_Code" /f
```

### bat 파일 주의사항
- 줄바꿈은 반드시 **CRLF** (LF면 cmd.exe가 파싱 실패)
- `scripts/*.bat` 안의 `PROJECT_DIR`, `PYTHON` 경로를 새 머신에 맞게 수정

### 로그 확인

```cmd
type logs\scheduled_layer2.log
type logs\scheduled_batch.log
type logs\listener.log
```

---

## macOS 세팅

### sh 스크립트 생성

bat 파일 대신 sh 스크립트를 사용합니다. 예시 (`scripts/scheduled_layer2.sh`):

```bash
#!/bin/bash
PROJECT_DIR="/path/to/report-collector"
PYTHON="$PROJECT_DIR/.venv/bin/python"
DUMP_PATH="$PROJECT_DIR/data/layer2_scheduled_inputs.jsonl"
OUTPUT_PATH="$PROJECT_DIR/data/layer2_scheduled_outputs.jsonl"
LIMIT=100
LOG_FILE="$PROJECT_DIR/logs/scheduled_layer2.log"

cd "$PROJECT_DIR" || exit 1

echo "========================================"          >> "$LOG_FILE"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Layer2 schedule START (limit=$LIMIT)" >> "$LOG_FILE"

# Step 1: dump
rm -f "$DUMP_PATH"
"$PYTHON" run_analysis.py --dump-layer2 --dump-layer2-path "$DUMP_PATH" --limit $LIMIT >> "$LOG_FILE" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Step 1 exit code: $?" >> "$LOG_FILE"

if [ ! -f "$DUMP_PATH" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] No dump file. Nothing to process." >> "$LOG_FILE"
    echo "========================================"      >> "$LOG_FILE"
    exit 0
fi

# Step 2: claude -p 처리
"$PYTHON" scripts/claude_layer2.py --input "$DUMP_PATH" --output "$OUTPUT_PATH" --concurrency 1 --timeout 180 >> "$LOG_FILE" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Step 2 exit code: $?" >> "$LOG_FILE"

# Step 3: DB import
"$PYTHON" scripts/import_layer2.py --input "$OUTPUT_PATH" --apply >> "$LOG_FILE" 2>&1
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Step 3 exit code: $?" >> "$LOG_FILE"

echo "[$(date '+%Y-%m-%d %H:%M:%S')] Layer2 schedule END" >> "$LOG_FILE"
echo "========================================"          >> "$LOG_FILE"
```

```bash
chmod +x scripts/scheduled_layer2.sh
chmod +x scripts/scheduled_batch_submit.sh
chmod +x scripts/scheduled_batch_recover.sh
chmod +x scripts/start_listener.sh
```

### launchd 등록 (macOS 권장 방식)

crontab 대신 launchd plist를 사용합니다.

#### Layer2 Claude Code (5시간마다)

`~/Library/LaunchAgents/com.report-collector.layer2-claude.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.report-collector.layer2-claude</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/report-collector/scripts/scheduled_layer2.sh</string>
    </array>
    <key>StartInterval</key>
    <integer>18000</integer>
    <key>StandardOutPath</key>
    <string>/path/to/report-collector/logs/launchd_layer2.log</string>
    <key>StandardErrorPath</key>
    <string>/path/to/report-collector/logs/launchd_layer2_err.log</string>
</dict>
</plist>
```

#### Batch 제출 (매일 03시)

`~/Library/LaunchAgents/com.report-collector.batch-submit.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.report-collector.batch-submit</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/report-collector/scripts/scheduled_batch_submit.sh</string>
    </array>
    <key>StartCalendarInterval</key>
    <dict>
        <key>Hour</key>
        <integer>3</integer>
        <key>Minute</key>
        <integer>0</integer>
    </dict>
</dict>
</plist>
```

#### Batch 수거 (매일 04시)

위와 동일 구조, `Hour`를 `4`로 변경.

#### 리스너 (로그인 시 자동 실행 + 크래시 시 재시작)

`~/Library/LaunchAgents/com.report-collector.listener.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.report-collector.listener</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/report-collector/scripts/start_listener.sh</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/path/to/report-collector/logs/listener.log</string>
    <key>StandardErrorPath</key>
    <string>/path/to/report-collector/logs/listener_err.log</string>
</dict>
</plist>
```

### launchd 등록/관리

```bash
# 등록
launchctl load ~/Library/LaunchAgents/com.report-collector.layer2-claude.plist
launchctl load ~/Library/LaunchAgents/com.report-collector.batch-submit.plist
launchctl load ~/Library/LaunchAgents/com.report-collector.batch-recover.plist
launchctl load ~/Library/LaunchAgents/com.report-collector.listener.plist

# 상태 확인
launchctl list | grep report-collector

# 수동 실행
launchctl start com.report-collector.layer2-claude

# 해제
launchctl unload ~/Library/LaunchAgents/com.report-collector.layer2-claude.plist
```

### macOS 주의사항
- `KeepAlive: true`로 리스너가 죽으면 자동 재시작
- 절전 모드에서는 launchd 작업이 실행 안 됨 → `caffeinate` 또는 에너지 설정에서 잠자기 방지
- `/path/to/` 부분을 실제 경로로 변경

---

## 체크리스트 (머신 이전 시)

- [ ] Python 3.10+ 설치
- [ ] Node.js + Claude Code CLI 설치
- [ ] Claude Code 로그인 (`claude login`)
- [ ] 레포 클론 + venv + 의존성
- [ ] 환경변수 설정 (.env 또는 시스템 변수)
- [ ] Telegram 세션 파일 복사 또는 재인증
- [ ] PDF 저장 경로 생성 + settings.py의 `pdf_base_path` 수정
- [ ] DB 마이그레이션 실행
- [ ] 스크립트 내 경로 수정 (PROJECT_DIR, PYTHON 등)
- [ ] Windows: bat CRLF 확인 / macOS: sh 실행 권한
- [ ] 스케줄러 등록
- [ ] 수동 테스트 실행 → 로그 확인
