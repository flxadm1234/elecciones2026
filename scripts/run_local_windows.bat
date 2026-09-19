@echo off
REM ================================================================
REM  run_local_windows.bat - Arranca servidor Django en modo local
REM ================================================================
setlocal
cd /d "%~dp0\.."

if not exist .venv\Scripts\activate.bat (
    echo [ERROR] Falta el entorno virtual. Ejecuta setup_local_windows.bat primero.
    exit /b 1
)
call .venv\Scripts\activate.bat

echo ================================================================
echo  SISTEMA DE AUDITORIA DE ACTAS - Modo Local
echo  URL:     http://127.0.0.1:8000/admin/
echo  Admin:   admin / Admin12345
echo  Presiona CTRL+C para salir.
echo ================================================================
python manage.py runserver 0.0.0.0:8000
