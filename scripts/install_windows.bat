@echo off
REM Instalación de ShortsFactory en Windows (requiere Python 3.11+ en el PATH)
setlocal
cd /d "%~dp0\.."
where python >nul 2>nul || (echo [ERROR] No se encuentra Python. Instala Python 3.11+ desde python.org & exit /b 1)
python -m venv .venv || exit /b 1
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt || exit /b 1
python -m playwright install chromium || exit /b 1
where ffmpeg >nul 2>nul || echo [AVISO] FFmpeg no está en el PATH. Descárgalo (ver README) e indica la ruta en Ajustes.
echo.
echo Instalación completada. Para iniciar:  scripts\run_windows.bat
endlocal
