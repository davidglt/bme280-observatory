@echo off
REM BME280 Observatory launcher for Windows 11 (NYX)
REM Starts the sensor reader and SharpCap HTTP server in parallel

SETLOCAL
SET VENV=%~dp0..\.venv\Scripts\activate.bat
SET SENSOR=%~dp0..\sensor\bme280_ch341a.py
SET SHARPCAP=%~dp0..\sharpcap\sharpcap_conditions.py

IF NOT EXIST "%~dp0..\config.yaml" (
    echo [ERROR] config.yaml not found. Copy config.example.yaml to config.yaml and edit it.
    pause
    exit /b 1
)

call "%VENV%"

echo Starting BME280 sensor reader...
start "BME280 Sensor" cmd /k python "%SENSOR%"

echo Starting SharpCap conditions server...
start "SharpCap Conditions" cmd /k python "%SHARPCAP%"

echo.
echo Both services started. Close the windows to stop them.
pause
