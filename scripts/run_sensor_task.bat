@echo off
REM BME280 Observatory - sensor launcher for Windows Task Scheduler.
REM Uses pythonw.exe (no console window) with absolute paths.
REM Appends stdout and stderr to logs\sensor.log.

SET ROOT=C:\astro\bme280-observatory
SET PYTHONW=%ROOT%\.venv\Scripts\pythonw.exe
SET PYTHON=%ROOT%\.venv\Scripts\python.exe
SET SCRIPT=%ROOT%\sensor\bme280_ch341t_v3.py
SET LOGDIR=%ROOT%\logs
SET LOGFILE=%LOGDIR%\sensor.log

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

if not exist "%PYTHONW%" (
    echo [ERROR] pythonw.exe not found: %PYTHONW% >> "%LOGFILE%"
    exit /b 1
)

if not exist "%SCRIPT%" (
    echo [ERROR] Script not found: %SCRIPT% >> "%LOGFILE%"
    exit /b 1
)

echo [START] %DATE% %TIME% >> "%LOGFILE%"
"%PYTHONW%" "%SCRIPT%" >> "%LOGFILE%" 2>&1

echo [EXIT] %DATE% %TIME% errorlevel=%errorlevel% >> "%LOGFILE%"
