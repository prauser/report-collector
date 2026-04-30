@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ========================================
REM  Layer2 Codex CLI 스케줄 작업
REM  - 5시간마다 Windows Task Scheduler로 실행
REM  - 200건 dump -> codex exec 처리 -> DB import
REM ========================================

for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
set "PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "DUMP_PATH=%PROJECT_DIR%\data\layer2_codex_scheduled_inputs.jsonl"
set "OUTPUT_PATH=%PROJECT_DIR%\data\layer2_codex_scheduled_outputs.jsonl"
set "LIMIT=200"
set "LOG_FILE=%PROJECT_DIR%\logs\scheduled_codex_layer2.log"

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo [%date% %time%] FATAL: cd failed >> "%LOG_FILE%"
    exit /b 1
)

echo ======================================== >> "%LOG_FILE%"
echo [%date% %time%] Layer2 Codex schedule START (limit=%LIMIT%) >> "%LOG_FILE%"

if not exist "%PYTHON%" (
    echo [%date% %time%] FATAL: python not found at %PYTHON% >> "%LOG_FILE%"
    goto :END
)

if exist "%DUMP_PATH%" del "%DUMP_PATH%"

echo [%date% %time%] Step 1: dumping %LIMIT% items >> "%LOG_FILE%"
"%PYTHON%" run_analysis.py --dump-layer2 --dump-layer2-path "%DUMP_PATH%" --limit %LIMIT% >> "%LOG_FILE%" 2>&1
echo [%date% %time%] Step 1 exit code: %errorlevel% >> "%LOG_FILE%"

if not exist "%DUMP_PATH%" (
    echo [%date% %time%] No dump file created. Nothing to process. >> "%LOG_FILE%"
    goto :END
)

echo [%date% %time%] Step 2: codex_layer2.py processing >> "%LOG_FILE%"
"%PYTHON%" scripts\codex_layer2.py --input "%DUMP_PATH%" --output "%OUTPUT_PATH%" --concurrency 1 --max-daily %LIMIT% --timeout 240 >> "%LOG_FILE%" 2>&1
echo [%date% %time%] Step 2 exit code: %errorlevel% >> "%LOG_FILE%"

echo [%date% %time%] Step 3: import_layer2.py --apply >> "%LOG_FILE%"
"%PYTHON%" scripts\import_layer2.py --input "%OUTPUT_PATH%" --apply >> "%LOG_FILE%" 2>&1
echo [%date% %time%] Step 3 exit code: %errorlevel% >> "%LOG_FILE%"

:END
echo [%date% %time%] Layer2 Codex schedule END >> "%LOG_FILE%"
echo ======================================== >> "%LOG_FILE%"

endlocal
