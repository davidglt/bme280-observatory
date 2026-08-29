@echo off
REM Elimina la tarea programada BME280Observatory del Programador de tareas de Windows.
REM Requiere ejecutar como Administrador.

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Este script debe ejecutarse como Administrador.
    pause
    exit /b 1
)

SET TASK_NAME=BME280Observatory

echo Eliminando tarea "%TASK_NAME%"...
schtasks /delete /tn "%TASK_NAME%" /f

if %errorlevel% equ 0 (
    echo [OK] Tarea eliminada.
) else (
    echo [WARN] La tarea no existia o no se pudo eliminar.
)
pause
