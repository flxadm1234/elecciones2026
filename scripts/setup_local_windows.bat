@echo off
REM ================================================================
REM  setup_local_windows.bat - Configuracion local (Windows)
REM ================================================================
setlocal enabledelayedexpansion

cd /d "%~dp0\.."

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python no se encontro. Instala Python 3.10+ y agregalo al PATH.
    exit /b 1
)

echo [1/6] Copiando .env desde .env.example ...
if not exist .env (
    copy .env.example .env >nul
) else (
    echo   .env ya existe, se mantiene.
)

echo [2/6] Creando entorno virtual .venv ...
if not exist .venv (
    python -m venv .venv
    if errorlevel 1 ( echo [ERROR] Fallo venv. & exit /b 2 )
)
call .venv\Scripts\activate.bat || goto :no_venv

echo [3/6] Actualizando pip y dependencias ...
python -m pip install --upgrade pip setuptools wheel >nul
pip install -r requirements.txt
if errorlevel 1 ( echo [ERROR] Fallo la instalacion. & exit /b 3 )

if not exist media\nul mkdir media
if not exist media\images\nul mkdir media\images
if not exist staticfiles\nul mkdir staticfiles
if not exist logs\nul mkdir logs

echo [4/6] Migraciones y usuario admin ...
python manage.py makemigrations core
python manage.py migrate
python manage.py ensure_admin

echo [5/6] Cargando catalogos demo (Lima, provincias, distritos, organizaciones) ...
python manage.py seed_catalogs

echo [6/6] Collect static ...
python manage.py collectstatic --noinput -v 0

echo.
echo ========================================================================
echo  PROYECTO LISTO.
echo  Ejecuta: run_local_windows.bat para arrancar el servidor Django.
echo  Credenciales demo: admin / Admin12345
echo  URL: http://127.0.0.1:8000/admin/
echo ========================================================================
goto :eof

:no_venv
echo [ERROR] No se pudo activar el entorno virtual.
exit /b 99
