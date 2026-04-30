@echo off
chcp 65001 >nul
REM ========================================
REM  Schedule switch: Split Sonnet -> Split Opus low
REM  - Disable Layer2_Claude_Codex_Split
REM  - Enable Layer2_Claude_Codex_Split_Opus
REM ========================================

for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
set "LOG_FILE=%PROJECT_DIR%\logs\schedule_switch.log"

echo ======================================== >> "%LOG_FILE%"
echo [%date% %time%] Schedule switch: Split Sonnet -^> Split Opus >> "%LOG_FILE%"

schtasks /change /tn "Layer2_Claude_Codex_Split" /disable >> "%LOG_FILE%" 2>&1
echo [%date% %time%] Split Sonnet disable exit code: %errorlevel% >> "%LOG_FILE%"

schtasks /change /tn "Layer2_Claude_Codex_Split_Opus" /enable >> "%LOG_FILE%" 2>&1
echo [%date% %time%] Split Opus enable exit code: %errorlevel% >> "%LOG_FILE%"

echo [%date% %time%] Switch complete >> "%LOG_FILE%"
echo ======================================== >> "%LOG_FILE%"
