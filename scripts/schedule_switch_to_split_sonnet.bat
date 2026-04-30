@echo off
chcp 65001 >nul
REM ========================================
REM  Schedule switch: Split Opus -> Split Sonnet
REM  - Enable Layer2_Claude_Codex_Split
REM  - Disable Layer2_Claude_Codex_Split_Opus
REM ========================================

for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
set "LOG_FILE=%PROJECT_DIR%\logs\schedule_switch.log"

echo ======================================== >> "%LOG_FILE%"
echo [%date% %time%] Schedule switch: Split Opus -^> Split Sonnet >> "%LOG_FILE%"

schtasks /change /tn "Layer2_Claude_Codex_Split" /enable >> "%LOG_FILE%" 2>&1
echo [%date% %time%] Split Sonnet enable exit code: %errorlevel% >> "%LOG_FILE%"

schtasks /change /tn "Layer2_Claude_Codex_Split_Opus" /disable >> "%LOG_FILE%" 2>&1
echo [%date% %time%] Split Opus disable exit code: %errorlevel% >> "%LOG_FILE%"

echo [%date% %time%] Switch complete >> "%LOG_FILE%"
echo ======================================== >> "%LOG_FILE%"
