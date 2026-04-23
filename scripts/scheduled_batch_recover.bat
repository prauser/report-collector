@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ========================================
REM  Layer2 Anthropic Batch 결과 수거
REM  - 매일 04시 실행 (Task Scheduler)
REM  - pending 배치 결과를 DB에 저장
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
echo [%date% %time%] Batch recover START >> "%LOG_FILE%"

if not exist "%PYTHON%" (
    echo [%date% %time%] FATAL: python not found at %PYTHON% >> "%LOG_FILE%"
    goto :END
)

echo [%date% %time%] Step 1: recover_batches.py --from-pending --apply >> "%LOG_FILE%"
"%PYTHON%" scripts\recover_batches.py --from-pending --apply >> "%LOG_FILE%" 2>&1
echo [%date% %time%] Step 1 exit code: %errorlevel% >> "%LOG_FILE%"

:END
echo [%date% %time%] Batch recover END >> "%LOG_FILE%"
echo ======================================== >> "%LOG_FILE%"

endlocal
