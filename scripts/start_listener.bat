@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ========================================
REM  실시간 리스너 (Telegram 수집 + PDF)
REM  - 로그온 시 자동 실행
REM ========================================

set "PROJECT_DIR=C:\Users\praus\Projects\report-collector"
set "PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "LOG_FILE=%PROJECT_DIR%\logs\listener.log"

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo [%date% %time%] FATAL: cd failed >> "%LOG_FILE%"
    exit /b 1
)

echo [%date% %time%] Listener START >> "%LOG_FILE%"

if not exist "%PYTHON%" (
    echo [%date% %time%] FATAL: python not found at %PYTHON% >> "%LOG_FILE%"
    exit /b 1
)

"%PYTHON%" main.py >> "%LOG_FILE%" 2>&1

echo [%date% %time%] Listener EXIT (code=%errorlevel%) >> "%LOG_FILE%"

endlocal
