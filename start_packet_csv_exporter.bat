@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "APP_NAME=GFL2 Packet CSV Exporter"
set "APP_ROOT=%~dp0"
set "APP_VERSION=1.5.0"
set "VENV_DIR=.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "LOG_DIR=logs"
set "BOOT_LOG=%LOG_DIR%\startup.log"
set "HELPER_DIR=bin"
set "HELPER_EXE=%HELPER_DIR%\windows-packet-capture.exe"
set "HELPER_VER=%HELPER_DIR%\windows-packet-capture.version"

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%" >nul 2>&1
>"%BOOT_LOG%" echo [%date% %time%] %APP_NAME% launcher start
>>"%BOOT_LOG%" echo Main GUI remains non-elevated; capture uses a separate administrator helper.

call :ensure_environment
if errorlevel 1 goto :fail
call :ensure_capture_helper
if errorlevel 1 goto :fail

if "%~1"=="" (
  >>"%BOOT_LOG%" echo Launching GUI.
  "%VENV_PY%" -m gfl2_packet_csv.gui >>"%BOOT_LOG%" 2>&1
) else (
  >>"%BOOT_LOG%" echo Launching CLI: %*
  "%VENV_PY%" -m gfl2_packet_csv.cli %* >>"%BOOT_LOG%" 2>&1
)
set "APP_RC=%errorlevel%"
if not "%APP_RC%"=="0" goto :runtime_failed
exit /b 0

:ensure_environment
if not exist "%VENV_PY%" (
  echo [%APP_NAME%] Creating isolated Python environment...
  >>"%BOOT_LOG%" echo Creating virtual environment.
  py -3.13 -m venv "%VENV_DIR%" >>"%BOOT_LOG%" 2>&1 || py -3.12 -m venv "%VENV_DIR%" >>"%BOOT_LOG%" 2>&1 || py -3.11 -m venv "%VENV_DIR%" >>"%BOOT_LOG%" 2>&1 || python -m venv "%VENV_DIR%" >>"%BOOT_LOG%" 2>&1
  if errorlevel 1 exit /b 1
)

REM Verify imports and that PyDivert exposes a WinDivert class.
REM recv(timeout=...) is intentionally NOT required: PyDivert 3.1.3 uses blocking recv(),
REM and the capture backend already supports both API variants safely.
"%VENV_PY%" -c "import PySide6,psutil,pydivert,gfl2_packet_csv; D=getattr(pydivert,'WinDivert',None) or getattr(pydivert,'Divert',None); assert D is not None; from gfl2_packet_csv.gui import main" >nul 2>>"%BOOT_LOG%"
if not errorlevel 1 exit /b 0

echo [%APP_NAME%] Runtime is incomplete. Repairing dependencies...
>>"%BOOT_LOG%" echo Runtime validation failed. Repair install starts.
"%VENV_PY%" -m pip install --disable-pip-version-check --upgrade pip setuptools wheel >>"%BOOT_LOG%" 2>&1
if errorlevel 1 exit /b 1
"%VENV_PY%" -m pip install --disable-pip-version-check --upgrade -e . >>"%BOOT_LOG%" 2>&1
if errorlevel 1 exit /b 1
"%VENV_PY%" -c "import PySide6,psutil,pydivert,gfl2_packet_csv; D=getattr(pydivert,'WinDivert',None) or getattr(pydivert,'Divert',None); assert D is not None; from gfl2_packet_csv.gui import main" >>"%BOOT_LOG%" 2>&1
if errorlevel 1 exit /b 1
>>"%BOOT_LOG%" echo Runtime validation passed after repair.
exit /b 0

:ensure_capture_helper
set "NEED_HELPER=1"
if exist "%HELPER_EXE%" if exist "%HELPER_VER%" (
  set /p INSTALLED_HELPER_VER=<"%HELPER_VER%"
  if "!INSTALLED_HELPER_VER!"=="%APP_VERSION%" set "NEED_HELPER=0"
)
if "%NEED_HELPER%"=="0" exit /b 0

echo [%APP_NAME%] Preparing Windows capture helper...
>>"%BOOT_LOG%" echo Building windows-packet-capture.exe for version %APP_VERSION%.
if not exist "%HELPER_DIR%" mkdir "%HELPER_DIR%" >nul 2>&1
"%VENV_PY%" -m pip install --disable-pip-version-check --upgrade pyinstaller >>"%BOOT_LOG%" 2>&1
if errorlevel 1 exit /b 1
if exist ".helper-dist" rmdir /s /q ".helper-dist"
if exist ".helper-build" rmdir /s /q ".helper-build"
"%VENV_PY%" -m PyInstaller --noconfirm --clean --distpath .helper-dist --workpath .helper-build "%APP_ROOT%packaging\helper.spec" >>"%BOOT_LOG%" 2>&1
if errorlevel 1 exit /b 1
if not exist ".helper-dist\windows-packet-capture.exe" exit /b 1
copy /y ".helper-dist\windows-packet-capture.exe" "%HELPER_EXE%" >nul
if errorlevel 1 exit /b 1
>"%HELPER_VER%" echo %APP_VERSION%
rmdir /s /q ".helper-dist" >nul 2>&1
rmdir /s /q ".helper-build" >nul 2>&1
>>"%BOOT_LOG%" echo Windows capture helper prepared.
exit /b 0

:runtime_failed
echo.
echo [%APP_NAME%] The program exited unexpectedly with code %APP_RC%.
echo Startup/error log: %BOOT_LOG%
echo.
powershell -NoProfile -Command "if(Test-Path '%BOOT_LOG%'){Get-Content '%BOOT_LOG%' -Tail 30}"
echo.
pause
exit /b %APP_RC%

:fail
echo.
echo [%APP_NAME%] Environment/helper preparation failed.
echo Startup/error log: %BOOT_LOG%
echo.
powershell -NoProfile -Command "if(Test-Path '%BOOT_LOG%'){Get-Content '%BOOT_LOG%' -Tail 50}"
echo.
pause
exit /b 1
