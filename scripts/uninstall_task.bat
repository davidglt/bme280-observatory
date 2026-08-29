@echo off
REM Removes the BME280Observatory scheduled task from Windows Task Scheduler.
REM Must be run as Administrator.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] This script must be run as Administrator.
    pause
    exit /b 1
)

SET TASK_NAME=BME280Observatory

echo Removing task "%TASK_NAME%"...
schtasks /delete /tn "%TASK_NAME%" /f

if %errorlevel% equ 0 (
    echo [OK] Task removed.
) else (
    echo [WARN] Task did not exist or could not be removed.
)
pause
