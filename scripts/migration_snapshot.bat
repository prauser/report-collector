@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

REM ========================================
REM  Migration snapshot: state files only
REM  Usage: scripts\migration_snapshot.bat [dest_root]
REM    default dest_root = %USERPROFILE%\Desktop
REM  PDFs excluded - use robocopy separately.
REM ========================================

pushd "%~dp0.." >nul
set "PROJECT_DIR=%CD%"
popd >nul

set "DEST_ROOT=%~1"
if "%DEST_ROOT%"=="" set "DEST_ROOT=%USERPROFILE%\Desktop"

for /f %%I in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmm"') do set "TS=%%I"
set "DEST=%DEST_ROOT%\report-collector-snapshot-%TS%"

echo === 스냅샷 생성 ===
echo source: %PROJECT_DIR%
echo dest:   %DEST%
echo.

mkdir "%DEST%" 2>nul
mkdir "%DEST%\config" 2>nul
mkdir "%DEST%\logs" 2>nul
mkdir "%DEST%\data" 2>nul

cd /d "%PROJECT_DIR%"

echo --- 필수 (시크릿/세션/in-flight) ---
call :copy_if "config\.env"                          CRITICAL
call :copy_if "report_collector.session"             CRITICAL
call :copy_if "logs\pending_batches.jsonl"           CRITICAL
call :copy_if "data\layer2_scheduled_inputs.jsonl"   OPTIONAL
call :copy_if "data\layer2_scheduled_outputs.jsonl"  OPTIONAL

echo.
echo --- 이력/캐시 ---
call :copy_if "logs\markdown_failures.csv"           OPTIONAL
call :copy_if "logs\layer2_validation_failures.csv"  OPTIONAL
call :copy_if "logs\layer2_sanitized.csv"            OPTIONAL
call :copy_if "logs\no_markdown_reports.csv"         OPTIONAL
call :copy_if "logs\crash_run_analysis.log"          OPTIONAL
call :copy_if "backfill_dates_cache.json"            OPTIONAL

echo.

REM SNAPSHOT_INFO.txt 작성
set "INFO=%DEST%\SNAPSHOT_INFO.txt"
> "%INFO%" echo Snapshot timestamp: %TS%
>> "%INFO%" echo Source host: %COMPUTERNAME%
>> "%INFO%" echo Source path: %PROJECT_DIR%
>> "%INFO%" echo.
>> "%INFO%" echo === pending_batches.jsonl ===
if exist "logs\pending_batches.jsonl" (
    for /f %%C in ('type "logs\pending_batches.jsonl" ^| find /c /v ""') do (
        >> "%INFO%" echo lines=%%C
        if %%C gtr 0 (
            echo [WARN] in-flight Anthropic Batch %%C건 존재 ^(SNAPSHOT_INFO.txt에 기록됨^)
        )
    )
) else (
    >> "%INFO%" echo ^(not present^)
)
>> "%INFO%" echo.
>> "%INFO%" echo === 미포함 - 별도 이관 ===
>> "%INFO%" echo - PDF base path [robocopy 권장]
>> "%INFO%" echo - %%USERPROFILE%%\.claude\ [Claude Code 토큰 - claude login 재실행으로 대체 가능]
>> "%INFO%" echo - .venv\ [새 머신에서 재생성]
>> "%INFO%" echo - DB [Railway 공유, 이관 불필요]

echo === 완료 ===
echo 스냅샷 위치: %DEST%
echo.
echo 다음 단계:
echo   1. 이 폴더를 새 머신으로 복사 (zip 또는 USB)
echo   2. PDF 별도 이관: robocopy F:\report-collector\pdfs ^<dest^> /E /R:3 /W:5 /MT:8
echo   3. 새 머신에서 docs\windows_setup.md 따라 세팅
echo   4. 새 머신 schtasks 등록 후 이 머신에서 scripts\disable_schedules.bat 실행
goto :eof

:copy_if
REM %1=경로, %2=CRITICAL/OPTIONAL
if exist "%~1" (
    copy /Y "%~1" "%DEST%\%~1" >nul
    for %%S in ("%~1") do echo [OK]   %~1 ^(%%~zS bytes^)
) else (
    if /i "%~2"=="CRITICAL" (
        echo [MISS] %~1 ^(CRITICAL - 확인 필요^)
    ) else (
        echo [skip] %~1 ^(없음^)
    )
)
goto :eof
