@echo off
chcp 65001 >nul
REM ========================================
REM  Disable all report-collector schtasks on this machine.
REM  Run on the OLD machine after the new machine is fully set up.
REM ========================================

echo === 스케줄 작업 비활성화 ===
echo.

call :disable_task "Report_Listener"
call :disable_task "Layer2_Batch_Submit"
call :disable_task "Layer2_Batch_Recover"
call :disable_task "Layer2_Claude_Code"
call :disable_task "Layer2_Claude_Code_Opus"

echo.
echo === 현재 돌고 있는 python 프로세스 ===
echo (main.py / run_analysis.py가 보이면 수동 종료하세요)
echo.
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter ""Name='python.exe'"" | Where-Object { $_.CommandLine -match 'main\.py|run_analysis\.py|claude_layer2\.py|import_layer2\.py|recover_batches\.py' } | Select-Object ProcessId,@{n='Cmd';e={if($_.CommandLine){$_.CommandLine.Substring(0,[Math]::Min(120,$_.CommandLine.Length))}}} | Format-Table -AutoSize"

echo.
echo === pending_batches.jsonl 상태 ===
set "PROJECT_DIR=%~dp0.."
if exist "%PROJECT_DIR%\logs\pending_batches.jsonl" (
    for /f %%C in ('type "%PROJECT_DIR%\logs\pending_batches.jsonl" ^| find /c /v ""') do (
        if %%C gtr 0 (
            echo [WARN] in-flight Anthropic Batch %%C건. 새 머신으로 logs\pending_batches.jsonl 반드시 이관!
        ) else (
            echo [OK] in-flight batch 없음
        )
    )
) else (
    echo [OK] pending_batches.jsonl 없음
)

echo.
echo 완료. 이관 준비가 끝났으면 scripts\migration_snapshot.bat 실행.
goto :eof

:disable_task
schtasks /change /tn %~1 /disable >nul 2>&1
if errorlevel 1 (
    echo [SKIP] %~1 ^(등록 안 됨 또는 이미 비활성^)
) else (
    echo [OK]   %~1 disabled
)
goto :eof
