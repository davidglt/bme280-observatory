@echo off
REM Registers the BME280Observatory scheduled task in Windows Task Scheduler.
REM Must be run as Administrator.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] This script must be run as Administrator.
    pause
    exit /b 1
)

SET TASK_NAME=BME280Observatory
SET XML=%~dp0bme280-observatory.xml

if not exist "%XML%" (
    echo [ERROR] Task definition not found: %XML%
    pause
    exit /b 1
)

echo Registering task "%TASK_NAME%"...
schtasks /create /tn "%TASK_NAME%" /xml "%XML%" /f

if %errorlevel% equ 0 (
    echo [OK] Task registered successfully.
    echo To start it now without rebooting:
    echo   schtasks /run /tn "%TASK_NAME%"
) else (
    echo [ERROR] Failed to register the task.
)
pause
