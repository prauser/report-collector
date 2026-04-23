@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ========================================
REM  Layer2 Anthropic Batch API 제출
REM  - 매일 03시 실행 (Task Scheduler)
REM  - 300건 분석 + 배치 제출
REM ========================================

set "PROJECT_DIR=C:\Users\praus\Projects\report-collector"
set "PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "LOG_FILE=%PROJECT_DIR%\logs\scheduled_batch.log"

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo [%date% %time%] FATAL: cd failed >> "%LOG_FILE%"
    exit /b 1
)

echo ======================================== >> "%LOG_FILE%"
echo [%date% %time%] Batch submit START (limit=300) >> "%LOG_FILE%"

if not exist "%PYTHON%" (
    echo [%date% %time%] FATAL: python not found at %PYTHON% >> "%LOG_FILE%"
    goto :END
)

echo [%date% %time%] Step 1: run_analysis.py --limit 300 --batch-size 300 >> "%LOG_FILE%"
"%PYTHON%" run_analysis.py --limit 300 --batch-size 300 >> "%LOG_FILE%" 2>&1
echo [%date% %time%] Step 1 exit code: %errorlevel% >> "%LOG_FILE%"

:END
echo [%date% %time%] Batch submit END >> "%LOG_FILE%"
echo ======================================== >> "%LOG_FILE%"

endlocal
