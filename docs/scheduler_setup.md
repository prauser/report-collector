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

## 스케줄 작업 목록 (현재 운영 기준, 2026-04-25)

| 작업명 (schtasks) | 주기 | 시각 (KST) | 스크립트 | 설명 |
|---|---|---|---|---|
| `Report_Listener` | 로그온 시 (상시) | — | `scripts/start_listener.bat` | Telegram 실시간 수집 + S2a 분류 + PDF 3단계 fallback |
| `Layer2_Batch_Submit` | 매일 | **04:30** | `scripts/scheduled_batch_submit.bat` | `run_analysis.py --limit 300 --batch-size 300` → Anthropic Batch 제출 |
| `Layer2_Batch_Recover` | 매일 | **09:00** | `scripts/scheduled_batch_recover.bat` | `recover_batches.py --from-pending --apply` → 완료 batch DB 저장 |
| `Layer2_Claude_Code` | **5시간마다** | 04/09/14/19/00 (예) | `scripts/scheduled_layer2.bat` | Sonnet 메인 — dump 100건 → claude -p → import |
| `Layer2_Claude_Code_Opus` | 5시간마다 (기본 **Disabled**) | 08/13/18/23/04 | `scripts/scheduled_layer2_opus.bat` | Sonnet 한도 소진 시 폴백 (`--model opus --effort low`) |
| `Layer2_Schedule_Switch` | 일회성 | Sonnet 한도 리셋 시각 | `scripts/schedule_switch_to_sonnet.bat` | Opus → Sonnet 자동 전환 (Sonnet enable + Opus disable) |

---

## 스케줄 구조 및 상호작용

### 하루 타임라인 (정상 운영 시)

```
KST  00:00 ─── Layer2_Claude_Code (Sonnet)  [~3h 소요: dump→claude→import]
     04:00 ─── (Sonnet 끝날 무렵)
     04:30 ─── Layer2_Batch_Submit          [~1.5h 소요]
     05:00 ─── Layer2_Claude_Code (Sonnet)  [다음 5h 틱]
     06:00 ─── (Batch submit 완료)
     09:00 ─── Layer2_Batch_Recover         [~20m, 전날 batch 회수]
     10:00 ─── Layer2_Claude_Code (Sonnet)
     15:00 ─── Layer2_Claude_Code (Sonnet)
     20:00 ─── Layer2_Claude_Code (Sonnet)
```

- **Layer2 Claude Code 5h 간격**: `StartTime=2026-04-16T11:00 + PT5H`로 등록 → 연속 5h 틱. 재등록 시각에 따라 fire 시각은 달라질 수 있음
- **Batch 제출→수거 사이클**: 제출 04:30 → Anthropic 처리 ~30분 → 수거 09:00. 전날 제출분을 다음날 새벽 수거하는 구조

### Sonnet ↔ Opus 전환 메커니즘

Claude Code는 Sonnet 주간 한도가 있어서 소진되면 **Opus LOW effort로 폴백**하는 2단 구조:

1. **평상시**: `Layer2_Claude_Code` 만 Enabled. Sonnet 사용.
2. **Sonnet 한도 소진 시 (수동)**:
   ```cmd
   schtasks /change /tn "Layer2_Claude_Code" /disable
   schtasks /change /tn "Layer2_Claude_Code_Opus" /enable
   ```
3. **한도 리셋 시각에 자동 복귀**: `Layer2_Schedule_Switch` 태스크를 한번용으로 등록해두면 해당 시각에 자동으로 Sonnet enable + Opus disable.

Opus 라운드는 동일 dump/output 파일(`data/layer2_scheduled_*.jsonl`)을 공유하므로 resume이 자연스럽게 동작 — 이미 처리된 건은 skip됨 (`claude_layer2.py`의 resume 로직).

### 분석 파이프라인 2중 경로

| 경로 | 속도 | 비용 | 처리량 |
|---|---|---|---|
| **Anthropic Batch** (`scheduled_batch_*`) | 30분~수시간 | 저 (할인가) | 하루 ~282/300건 |
| **Claude Code Layer2** (`scheduled_layer2*`) | 건당 30초~2분 | 구독 한도 | 라운드당 ~100건 × 4~5회 = ~400-500건/일 |

두 경로를 병행하면 하루 ~700건 처리 가능 → analysis_pending 40k는 약 2개월 내 수렴.

### 동시 실행 안전성

`run_analysis.py`는 `utils/crash_logging.py:check_exclusive()`로 **sentinel + PID 라이브 체크**를 거침:
- `.analysis_running` 파일 존재 & PID 살아있음 → "다른 인스턴스 실행 중" 거부
- PID 죽었으면 stale lock으로 간주하고 제거 후 진행

→ Batch submit(04:30~06:00)과 Layer2 5h 틱(05:00)이 겹쳐도 한쪽이 거부당해 중복 실행 없음.

---

## Windows 세팅

### 스케줄러 등록 (관리자 CMD)

새 머신에 올릴 땐 아래 5개 작업을 등록. `<REPO>`를 실제 레포 경로로 치환 (bat 파일은 `%~dp0..`로 경로 자동 유도하므로 내용 수정 불필요).

```cmd
REM === 1. 리스너 (로그온 시 자동 실행) ===
schtasks /create /tn "Report_Listener" /tr "<REPO>\scripts\start_listener.bat" /sc ONLOGON /f

REM === 2. Batch 제출 (매일 04:30) ===
schtasks /create /tn "Layer2_Batch_Submit" /tr "<REPO>\scripts\scheduled_batch_submit.bat" /sc DAILY /st 04:30 /f

REM === 3. Batch 수거 (매일 09:00) ===
schtasks /create /tn "Layer2_Batch_Recover" /tr "<REPO>\scripts\scheduled_batch_recover.bat" /sc DAILY /st 09:00 /f

REM === 4. Layer2 Claude Code Sonnet (5시간마다, 기본 메인) ===
schtasks /create /tn "Layer2_Claude_Code" /tr "<REPO>\scripts\scheduled_layer2.bat" /sc HOURLY /mo 5 /st 00:00 /f

REM === 5. Layer2 Claude Code Opus 폴백 (5시간마다, 기본 비활성) ===
schtasks /create /tn "Layer2_Claude_Code_Opus" /tr "<REPO>\scripts\scheduled_layer2_opus.bat" /sc HOURLY /mo 5 /st 03:00 /f
schtasks /change /tn "Layer2_Claude_Code_Opus" /disable
```

> **시각 의미**:
> - Batch 제출 04:30 → 수거 09:00 사이 Anthropic 처리가 끝나도록 4h 30m 간격
> - Sonnet 5h 틱 00:00 시작 → 00/05/10/15/20 KST fire (Batch 작업과 부딪히면 `check_exclusive` sentinel이 한쪽 거부)
> - Opus 폴백은 03:00 시작으로 Sonnet과 엇갈리게 배치 (한쪽만 활성화해서 쓰니 실제 시각은 크게 안 중요)

### Sonnet → Opus 수동 전환 (한도 소진 시)

```cmd
schtasks /change /tn "Layer2_Claude_Code" /disable
schtasks /change /tn "Layer2_Claude_Code_Opus" /enable
```

### Opus → Sonnet 자동 전환 (한도 리셋 시각)

한도 리셋 시각을 알고 있으면 일회성 Schedule_Switch 태스크 등록:

```cmd
REM 예: 2026-04-24 07:30에 Sonnet 복귀
schtasks /create /tn "Layer2_Schedule_Switch" /tr "<REPO>\scripts\schedule_switch_to_sonnet.bat" /sc ONCE /st 07:30 /sd 2026-04-24 /f
```

`schedule_switch_to_sonnet.bat`이 실행되면서 Sonnet enable + Opus disable 수행.

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
