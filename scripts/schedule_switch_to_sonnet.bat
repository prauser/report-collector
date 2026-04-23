@echo off
chcp 65001 >nul
REM ========================================
REM  Schedule switch: Opus -> Sonnet
REM  Run after Sonnet weekly quota reset (04/24 07:00)
REM  - Enable Layer2_Claude_Code (Sonnet)
REM  - Disable Layer2_Claude_Code_Opus
REM ========================================
set "LOG_FILE=C:\Users\praus\Projects\report-collector\logs\schedule_switch.log"

echo ======================================== >> "%LOG_FILE%"
echo [%date% %time%] Schedule switch: Opus -^> Sonnet >> "%LOG_FILE%"

schtasks /change /tn "Layer2_Claude_Code" /enable >> "%LOG_FILE%" 2>&1
echo [%date% %time%] Sonnet enable exit code: %errorlevel% >> "%LOG_FILE%"

schtasks /change /tn "Layer2_Claude_Code_Opus" /disable >> "%LOG_FILE%" 2>&1
echo [%date% %time%] Opus disable exit code: %errorlevel% >> "%LOG_FILE%"

echo [%date% %time%] Switch complete >> "%LOG_FILE%"
echo ======================================== >> "%LOG_FILE%"
