@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ========================================
REM  Layer2 Claude + Codex split schedule
REM  - 300건 dump -> Claude 100건 + Codex 200건 병렬 처리 -> DB import
REM  - Claude 기본값: Sonnet
REM  - Opus low로 전환하려면 scheduled_layer2_split_opus.bat 사용
REM ========================================

for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
set "PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "LOG_FILE=%PROJECT_DIR%\logs\scheduled_layer2_split.log"
set "BOOTSTRAP_LOG=%PROJECT_DIR%\logs\scheduled_layer2_split_bootstrap.log"

set "LAYER2_SPLIT_CLAUDE_MODEL=claude-sonnet-4-6"
set "LAYER2_SPLIT_CLAUDE_EFFORT="
set "LAYER2_SPLIT_CLAUDE_CONCURRENCY=1"
set "LAYER2_SPLIT_CODEX_CONCURRENCY=3"

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo [%date% %time%] FATAL: cd failed >> "%LOG_FILE%"
    exit /b 1
)

if not exist "%PYTHON%" (
    echo [%date% %time%] FATAL: python not found at %PYTHON% >> "%LOG_FILE%"
    exit /b 1
)

"%PYTHON%" scripts\scheduled_layer2_split.py >> "%BOOTSTRAP_LOG%" 2>&1
set "EXIT_CODE=%errorlevel%"
echo [%date% %time%] scheduled_layer2_split.py exit code: %EXIT_CODE% >> "%LOG_FILE%"

endlocal
exit /b %EXIT_CODE%
