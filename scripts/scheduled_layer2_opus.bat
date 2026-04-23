@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ========================================
REM  Layer2 (Opus low effort) 유사시 수동 실행용
REM  - Sonnet 주간 한도 소진 등 장애 시 임시 사용
REM  - 동일 dump/output 파일 공유 (scheduled_layer2.bat과 호환)
REM  - 사용: scripts\scheduled_layer2_opus.bat
REM ========================================

set "PROJECT_DIR=C:\Users\praus\Projects\report-collector"
set "PYTHON=%PROJECT_DIR%\.venv\Scripts\python.exe"
set "DUMP_PATH=%PROJECT_DIR%\data\layer2_scheduled_inputs.jsonl"
set "OUTPUT_PATH=%PROJECT_DIR%\data\layer2_scheduled_outputs.jsonl"
set "LIMIT=100"
set "LOG_FILE=%PROJECT_DIR%\logs\scheduled_layer2.log"

cd /d "%PROJECT_DIR%"
if errorlevel 1 (
    echo [%date% %time%] FATAL: cd failed >> "%LOG_FILE%"
    exit /b 1
)

echo ======================================== >> "%LOG_FILE%"
echo [%date% %time%] Layer2 schedule START [OPUS LOW] (limit=%LIMIT%) >> "%LOG_FILE%"

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

echo [%date% %time%] Step 2: claude_layer2.py processing [OPUS LOW] >> "%LOG_FILE%"
"%PYTHON%" scripts\claude_layer2.py --input "%DUMP_PATH%" --output "%OUTPUT_PATH%" --concurrency 1 --timeout 180 --model opus --effort low >> "%LOG_FILE%" 2>&1
echo [%date% %time%] Step 2 exit code: %errorlevel% >> "%LOG_FILE%"

echo [%date% %time%] Step 3: import_layer2.py --apply >> "%LOG_FILE%"
"%PYTHON%" scripts\import_layer2.py --input "%OUTPUT_PATH%" --apply >> "%LOG_FILE%" 2>&1
echo [%date% %time%] Step 3 exit code: %errorlevel% >> "%LOG_FILE%"

:END
echo [%date% %time%] Layer2 schedule END [OPUS LOW] >> "%LOG_FILE%"
echo ======================================== >> "%LOG_FILE%"

endlocal
