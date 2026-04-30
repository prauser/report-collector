@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ========================================
REM  Layer2 Claude(Opus low) + Codex split schedule
REM  - Sonnet 한도 소진 시 scheduled_layer2_split.bat 대신 사용
REM  - 300건 dump -> Claude Opus low 70건 + Codex 230건 병렬 처리 -> DB import
REM ========================================

for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
set "PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "LOG_FILE=%PROJECT_DIR%\logs\scheduled_layer2_split.log"
set "BOOTSTRAP_LOG=%PROJECT_DIR%\logs\scheduled_layer2_split_bootstrap.log"

set "LAYER2_SPLIT_CLAUDE_MODEL=opus"
set "LAYER2_SPLIT_CLAUDE_EFFORT=low"
set "LAYER2_SPLIT_TOTAL_LIMIT=300"
set "LAYER2_SPLIT_CLAUDE_LIMIT=70"
set "LAYER2_SPLIT_CODEX_LIMIT=230"
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
echo [%date% %time%] scheduled_layer2_split.py [OPUS LOW] exit code: %EXIT_CODE% >> "%LOG_FILE%"

endlocal
exit /b %EXIT_CODE%
