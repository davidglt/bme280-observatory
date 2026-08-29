@echo off
REM BME280 Observatory - sensor launcher for Windows Task Scheduler.
REM Redirects stdout and stderr to logs\sensor.log (appends).
REM WorkingDirectory must be set to C:\astro\bme280-observatory in the task XML.

SET ROOT=%~dp0..
SET PYTHON=%ROOT%\.venv\Scripts\python.exe
SET SCRIPT=%ROOT%\sensor\bme280_ch341t_v3.py
SET LOGDIR=%ROOT%\logs
SET LOGFILE=%LOGDIR%\sensor.log

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

"%PYTHON%" "%SCRIPT%" >> "%LOGFILE%" 2>&1
