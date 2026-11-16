@echo off
REM ==========================================================================
REM  ShortsFactory - Instalador para Windows 10/11
REM  Doble clic y listo. No necesita nada instalado de antemano.
REM  Instala: Python 3.12 (si falta), FFmpeg, la aplicacion, sus dependencias
REM  y el navegador Chromium para la subida. Crea accesos directos.
REM  Volver a ejecutarlo actualiza la aplicacion sin borrar tus datos.
REM ==========================================================================
setlocal EnableExtensions
chcp 65001 >nul
title Instalador de ShortsFactory

set "REPO=Sigma01-Gang/YoutubeLameBicho"
set "BRANCH=claude/relaxed-meitner-ui2vo2"
set "APP_ZIP_URL=https://github.com/%REPO%/archive/refs/heads/%BRANCH%.zip"
set "FFMPEG_URL=https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip"
set "PY_URL=https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe"

set "INSTALL_DIR=%LOCALAPPDATA%\ShortsFactory"
set "APP_DIR=%INSTALL_DIR%\app"
set "VENV=%INSTALL_DIR%\venv"
set "FF_DIR=%INSTALL_DIR%\ffmpeg"
set "TMPDIR=%TEMP%\shortsfactory_setup"
set "LOG=%INSTALL_DIR%\instalacion.log"
set "PS=powershell -NoProfile -ExecutionPolicy Bypass -Command"

if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
if exist "%TMPDIR%" rmdir /s /q "%TMPDIR%"
mkdir "%TMPDIR%"
echo ==== Instalacion %DATE% %TIME% ==== > "%LOG%"

cls
echo.
echo   ===============================================
echo         ShortsFactory  -  Instalador
echo   ===============================================
echo.
echo   Se instalara en: %INSTALL_DIR%
echo   Tardara unos minutos (descarga ~400 MB la primera vez).
echo   Deja esta ventana abierta hasta que termine.
echo.

REM ---------------------------------------------------------------- 1. Python
echo   [1/6] Buscando Python 3.11 o superior...
call :find_python
if defined PYEXE goto python_ok
echo         No encontrado. Instalando Python 3.12...
where winget >nul 2>&1
if errorlevel 1 goto python_download
winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements >> "%LOG%" 2>&1
call :find_python
if defined PYEXE goto python_ok
:python_download
echo         Descargando el instalador oficial de python.org...
call :download "%PY_URL%" "%TMPDIR%\python-setup.exe" || goto fail
"%TMPDIR%\python-setup.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0 >> "%LOG%" 2>&1
call :find_python
if not defined PYEXE (
    set "ERRMSG=No se pudo instalar Python. Instala Python 3.12 desde python.org y vuelve a ejecutar este archivo."
    goto fail
)
:python_ok
echo         OK: %PYEXE%
echo Python: %PYEXE% >> "%LOG%"

REM ------------------------------------------------------------ 2. Aplicacion
echo   [2/6] Instalando la aplicacion...
if exist "%~dp0shortsfactory\__main__.py" goto app_local
echo         Descargando desde GitHub...
call :download "%APP_ZIP_URL%" "%TMPDIR%\app.zip" || goto fail
%PS% "Expand-Archive -LiteralPath '%TMPDIR%\app.zip' -DestinationPath '%TMPDIR%\app' -Force" >> "%LOG%" 2>&1 || goto fail_unzip
if exist "%APP_DIR%" rmdir /s /q "%APP_DIR%"
%PS% "$d = Get-ChildItem -LiteralPath '%TMPDIR%\app' -Directory | Select-Object -First 1; Move-Item -LiteralPath $d.FullName -Destination '%APP_DIR%'" >> "%LOG%" 2>&1
if not exist "%APP_DIR%\shortsfactory\__main__.py" goto fail_unzip
goto app_ok
:app_local
echo         Copiando desde esta carpeta...
robocopy "%~dp0." "%APP_DIR%" /MIR /XD .git .venv venv __pycache__ .pytest_cache /NFL /NDL /NJH /NJS /NP >> "%LOG%" 2>&1
if errorlevel 8 (
    set "ERRMSG=No se pudieron copiar los archivos de la aplicacion."
    goto fail
)
:app_ok
echo         OK

REM ---------------------------------------------------------------- 3. FFmpeg
echo   [3/6] Instalando FFmpeg...
if exist "%FF_DIR%\bin\ffmpeg.exe" if exist "%FF_DIR%\bin\ffprobe.exe" (
    echo         Ya instalado.
    goto ffmpeg_ok
)
echo         Descargando FFmpeg (unos 150 MB)...
call :download "%FFMPEG_URL%" "%TMPDIR%\ffmpeg.zip" || goto fail
%PS% "Expand-Archive -LiteralPath '%TMPDIR%\ffmpeg.zip' -DestinationPath '%TMPDIR%\ffmpeg' -Force" >> "%LOG%" 2>&1 || goto fail_unzip
if exist "%FF_DIR%" rmdir /s /q "%FF_DIR%"
%PS% "$d = Get-ChildItem -LiteralPath '%TMPDIR%\ffmpeg' -Directory | Select-Object -First 1; Move-Item -LiteralPath $d.FullName -Destination '%FF_DIR%'" >> "%LOG%" 2>&1
if not exist "%FF_DIR%\bin\ffmpeg.exe" (
    set "ERRMSG=No se pudo instalar FFmpeg."
    goto fail
)
:ffmpeg_ok
echo         OK

REM ------------------------------------------------- 4. Entorno y dependencias
echo   [4/6] Instalando dependencias de Python (puede tardar)...
if not exist "%VENV%\Scripts\python.exe" "%PYEXE%" -m venv "%VENV%" >> "%LOG%" 2>&1
if not exist "%VENV%\Scripts\python.exe" (
    set "ERRMSG=No se pudo crear el entorno de Python."
    goto fail
)
"%VENV%\Scripts\python.exe" -m pip install --upgrade pip >> "%LOG%" 2>&1
"%VENV%\Scripts\python.exe" -m pip install -r "%APP_DIR%\requirements.txt" >> "%LOG%" 2>&1
if errorlevel 1 (
    set "ERRMSG=Fallo la instalacion de las dependencias. Revisa tu conexion a internet."
    goto fail
)
echo         OK

REM -------------------------------------------------------------- 5. Chromium
echo   [5/6] Instalando el navegador Chromium para la subida...
"%VENV%\Scripts\python.exe" -m playwright install chromium >> "%LOG%" 2>&1
if errorlevel 1 (
    set "ERRMSG=No se pudo instalar Chromium para Playwright."
    goto fail
)
echo         OK

REM ---------------------------------------------------- 6. Accesos directos
echo   [6/6] Creando accesos directos...
> "%INSTALL_DIR%\ShortsFactory.bat" (
    echo @echo off
    echo cd /d "%APP_DIR%"
    echo start "" "%VENV%\Scripts\pythonw.exe" -m shortsfactory
)
if exist "%APP_DIR%\DESINSTALAR.bat" copy /y "%APP_DIR%\DESINSTALAR.bat" "%INSTALL_DIR%\Desinstalar ShortsFactory.bat" >nul
%PS% "$w = New-Object -ComObject WScript.Shell; foreach ($dir in @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('Programs'))) { $s = $w.CreateShortcut((Join-Path $dir 'ShortsFactory.lnk')); $s.TargetPath = '%VENV%\Scripts\pythonw.exe'; $s.Arguments = '-m shortsfactory'; $s.WorkingDirectory = '%APP_DIR%'; $s.IconLocation = '%SystemRoot%\System32\shell32.dll,115'; $s.Description = 'Crear y subir YouTube Shorts e historias por partes'; $s.Save() }" >> "%LOG%" 2>&1
echo         OK

REM Comprobacion final
"%VENV%\Scripts\python.exe" -c "import sys; sys.path.insert(0, r'%APP_DIR%'); import shortsfactory.ui.main_window, cv2, pandas, playwright" >> "%LOG%" 2>&1
if errorlevel 1 (
    set "ERRMSG=La comprobacion final fallo. Revisa el registro."
    goto fail
)
rmdir /s /q "%TMPDIR%" >nul 2>&1

echo.
echo   ===============================================
echo     Instalacion completada correctamente
echo   ===============================================
echo.
echo   - Abre ShortsFactory con el icono del Escritorio o del menu Inicio.
echo   - La primera vez, un asistente te guiara (FFmpeg ya esta configurado).
echo   - Despues ve a Ajustes ^> Canales ^> "Iniciar sesion en Studio" para
echo     iniciar sesion a mano en cada canal (una sola vez).
echo.
choice /c SN /m "  Abrir ShortsFactory ahora"
if errorlevel 2 goto end
start "" "%VENV%\Scripts\pythonw.exe" -m shortsfactory
goto end

REM ============================================================== funciones
:find_python
set "PYEXE="
for %%V in (3.13 3.12 3.11) do call :try_python py -%%V
call :try_python python
for %%D in (Python313 Python312 Python311) do call :try_path "%LOCALAPPDATA%\Programs\Python\%%D\python.exe"
exit /b 0

:try_python
if defined PYEXE exit /b 0
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 exit /b 0
%* -c "import sys; print(sys.executable)" > "%TMPDIR%\py.txt" 2>nul
set /p PYEXE=<"%TMPDIR%\py.txt"
exit /b 0

:try_path
if defined PYEXE exit /b 0
if exist "%~1" call :try_python "%~1"
exit /b 0

:download
echo Descargando %~1 >> "%LOG%"
%PS% "$ProgressPreference = 'SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing -Uri '%~1' -OutFile '%~2'" >> "%LOG%" 2>&1
if exist "%~2" exit /b 0
set "ERRMSG=No se pudo descargar %~1. Comprueba tu conexion a internet."
exit /b 1

:fail_unzip
set "ERRMSG=No se pudo descomprimir un archivo descargado."
:fail
echo.
echo   ===============================================
echo     ERROR: la instalacion no se ha completado
echo   ===============================================
echo   %ERRMSG%
echo.
echo   Registro detallado: %LOG%
echo.
pause
exit /b 1

:end
endlocal
exit /b 0
