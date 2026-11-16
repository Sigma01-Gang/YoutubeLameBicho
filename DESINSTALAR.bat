@echo off
REM ShortsFactory - Desinstalador para Windows
setlocal EnableExtensions
chcp 65001 >nul
title Desinstalar ShortsFactory
set "INSTALL_DIR=%LOCALAPPDATA%\ShortsFactory"
set "DATA_DIR=%APPDATA%\ShortsFactory"

echo.
echo   Se eliminara ShortsFactory de: %INSTALL_DIR%
echo.
choice /c SN /m "  Continuar"
if errorlevel 2 exit /b 0

taskkill /f /im pythonw.exe /fi "WINDOWTITLE eq ShortsFactory*" >nul 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) { Remove-Item -LiteralPath (Join-Path $dir 'ShortsFactory.lnk') -ErrorAction SilentlyContinue }"

echo.
echo   Tus datos (base de datos, cola, sesiones de YouTube, copias) estan en:
echo   %DATA_DIR%
choice /c SN /m "  Borrar tambien tus datos y sesiones"
if errorlevel 2 goto keep
if exist "%DATA_DIR%" rmdir /s /q "%DATA_DIR%"
echo   Datos borrados.
:keep
echo   ShortsFactory se ha desinstalado. Pulsa una tecla para terminar.
pause >nul
REM El borrado se lanza al final porque este archivo puede estar dentro de la carpeta a borrar
start "" /min cmd /c "timeout /t 2 >nul & rmdir /s /q "%INSTALL_DIR%""
exit /b 0
