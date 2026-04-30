@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ========================================
REM  One-off batch submit after Layer2 finishes
REM ========================================

for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
set "PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "LOG_FILE=%PROJECT_DIR%\logs\batch_submit_after_layer2.log"
set "BOOTSTRAP_LOG=%PROJECT_DIR%\logs\batch_submit_after_layer2_bootstrap.log"

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo [%date% %time%] FATAL: cd failed >> "%LOG_FILE%"
    exit /b 1
)

if not exist "%PYTHON%" (
    echo [%date% %time%] FATAL: python not found at %PYTHON% >> "%LOG_FILE%"
    exit /b 1
)

"%PYTHON%" scripts\batch_submit_after_layer2.py >> "%BOOTSTRAP_LOG%" 2>&1
set "EXIT_CODE=%errorlevel%"
echo [%date% %time%] batch_submit_after_layer2.py exit code: %EXIT_CODE% >> "%LOG_FILE%"

endlocal
exit /b %EXIT_CODE%
