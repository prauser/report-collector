@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ========================================
REM  Layer2 Claude Code CLI 스케줄 작업
REM  - 5시간마다 Windows Task Scheduler로 실행
REM  - 70건 dump → claude -p 처리 → DB import
REM ========================================

for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
set "PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "DUMP_PATH=%PROJECT_DIR%\data\layer2_scheduled_inputs.jsonl"
set "OUTPUT_PATH=%PROJECT_DIR%\data\layer2_scheduled_outputs.jsonl"
set "LIMIT=100"
set "LOG_FILE=%PROJECT_DIR%\logs\scheduled_layer2.log"

REM 작업 디렉토리 이동
cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo [%date% %time%] FATAL: cd failed >> "%LOG_FILE%"
    exit /b 1
)

REM 로그 시작
echo ======================================== >> "%LOG_FILE%"
echo [%date% %time%] Layer2 schedule START (limit=%LIMIT%) >> "%LOG_FILE%"

REM Python 존재 확인
if not exist "%PYTHON%" (
    echo [%date% %time%] FATAL: python not found at %PYTHON% >> "%LOG_FILE%"
    goto :END
)

REM Step 1: 기존 dump 파일 삭제
if exist "%DUMP_PATH%" del "%DUMP_PATH%"

REM Step 2: analysis_pending 건 중 70건 dump
echo [%date% %time%] Step 1: dumping %LIMIT% items >> "%LOG_FILE%"
"%PYTHON%" run_analysis.py --dump-layer2 --dump-layer2-path "%DUMP_PATH%" --limit %LIMIT% >> "%LOG_FILE%" 2>&1
echo [%date% %time%] Step 1 exit code: %errorlevel% >> "%LOG_FILE%"

REM dump 파일 확인
if not exist "%DUMP_PATH%" (
    echo [%date% %time%] No dump file created. Nothing to process. >> "%LOG_FILE%"
    goto :END
)

REM Step 3: claude -p CLI로 Layer2 처리
echo [%date% %time%] Step 2: claude_layer2.py processing >> "%LOG_FILE%"
"%PYTHON%" scripts\claude_layer2.py --input "%DUMP_PATH%" --output "%OUTPUT_PATH%" --concurrency 1 --timeout 180 >> "%LOG_FILE%" 2>&1
echo [%date% %time%] Step 2 exit code: %errorlevel% >> "%LOG_FILE%"

REM Step 4: 결과를 DB에 import
echo [%date% %time%] Step 3: import_layer2.py --apply >> "%LOG_FILE%"
"%PYTHON%" scripts\import_layer2.py --input "%OUTPUT_PATH%" --apply >> "%LOG_FILE%" 2>&1
echo [%date% %time%] Step 3 exit code: %errorlevel% >> "%LOG_FILE%"

:END
echo [%date% %time%] Layer2 schedule END >> "%LOG_FILE%"
echo ======================================== >> "%LOG_FILE%"

endlocal
