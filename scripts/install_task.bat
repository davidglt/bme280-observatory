@echo off
REM Instala la tarea programada BME280Observatory en el Programador de tareas de Windows.
REM Requiere ejecutar como Administrador.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Este script debe ejecutarse como Administrador.
    pause
    exit /b 1
)

SET TASK_NAME=BME280Observatory
SET XML=%~dp0bme280-observatory.xml

if not exist "%XML%" (
    echo [ERROR] No se encuentra %XML%
    pause
    exit /b 1
)

echo Registrando tarea "%TASK_NAME%"...
schtasks /create /tn "%TASK_NAME%" /xml "%XML%" /f

if %errorlevel% equ 0 (
    echo [OK] Tarea registrada correctamente.
    echo Para iniciarla ahora sin reiniciar:
    echo   schtasks /run /tn "%TASK_NAME%"
) else (
    echo [ERROR] No se pudo registrar la tarea.
)
pause
