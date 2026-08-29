@echo off
REM BME280 Observatory - sensor launcher for Windows Task Scheduler.
REM Uses absolute paths to avoid WorkingDirectory resolution issues.
REM Appends stdout and stderr to logs\sensor.log.

SET ROOT=C:\astro\bme280-observatory
SET PYTHON=%ROOT%\.venv\Scripts\python.exe
SET SCRIPT=%ROOT%\sensor\bme280_ch341t_v3.py
SET LOGDIR=%ROOT%\logs
SET LOGFILE=%LOGDIR%\sensor.log

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

if not exist "%PYTHON%" (
    echo [ERROR] Python not found: %PYTHON% >> "%LOGFILE%"
    exit /b 1
)

if not exist "%SCRIPT%" (
    echo [ERROR] Script not found: %SCRIPT% >> "%LOGFILE%"
    exit /b 1
)

echo [START] %DATE% %TIME% >> "%LOGFILE%"
"%PYTHON%" "%SCRIPT%" >> "%LOGFILE%" 2>&1

echo [EXIT] %DATE% %TIME% errorlevel=%errorlevel% >> "%LOGFILE%"
