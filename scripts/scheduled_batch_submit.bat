@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ========================================
REM  Layer2 Anthropic Batch API 제출
REM  - 매일 03시 실행 (Task Scheduler)
REM  - 500건 분석 + 배치 제출
REM ========================================

for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
set "PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "LOG_FILE=%PROJECT_DIR%\logs\scheduled_batch.log"

set "BATCH_LIMIT=500"

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo [%date% %time%] FATAL: cd failed >> "%LOG_FILE%"
    exit /b 1
)

echo ======================================== >> "%LOG_FILE%"
echo [%date% %time%] Batch submit START (limit=%BATCH_LIMIT%) >> "%LOG_FILE%"

if not exist "%PYTHON%" (
    echo [%date% %time%] FATAL: python not found at %PYTHON% >> "%LOG_FILE%"
    goto :END
)

echo [%date% %time%] Step 1: run_analysis.py --limit %BATCH_LIMIT% --batch-size %BATCH_LIMIT% >> "%LOG_FILE%"
"%PYTHON%" run_analysis.py --limit %BATCH_LIMIT% --batch-size %BATCH_LIMIT% >> "%LOG_FILE%" 2>&1
echo [%date% %time%] Step 1 exit code: %errorlevel% >> "%LOG_FILE%"

:END
echo [%date% %time%] Batch submit END >> "%LOG_FILE%"
echo ======================================== >> "%LOG_FILE%"

endlocal
