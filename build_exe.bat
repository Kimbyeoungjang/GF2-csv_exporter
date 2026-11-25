@echo off
setlocal EnableExtensions
cd /d "%~dp0"
set "APP_ROOT=%~dp0"

if not exist .venv-build\Scripts\python.exe (
  py -3.13 -m venv .venv-build 2>nul || py -3.12 -m venv .venv-build || py -3.11 -m venv .venv-build
)
if errorlevel 1 goto :fail
call .venv-build\Scripts\activate.bat
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 goto :fail
python -m pip install --upgrade -e . pyinstaller
if errorlevel 1 goto :fail

if exist helper-dist rmdir /s /q helper-dist
if exist helper-build rmdir /s /q helper-build
python -m PyInstaller --noconfirm --clean --distpath helper-dist --workpath helper-build "%APP_ROOT%packaging\helper.spec"
if errorlevel 1 goto :fail
if not exist "helper-dist\windows-packet-capture.exe" goto :fail

python -m PyInstaller --noconfirm --clean "%APP_ROOT%packaging\packet.spec"
if errorlevel 1 goto :fail
if not exist "dist\GFL2-Packet-CSV-Exporter\GFL2-Packet-CSV-Exporter.exe" goto :fail
copy /y "helper-dist\windows-packet-capture.exe" "dist\GFL2-Packet-CSV-Exporter\windows-packet-capture.exe" >nul
if errorlevel 1 goto :fail

echo.
echo Build complete:
echo   dist\GFL2-Packet-CSV-Exporter\GFL2-Packet-CSV-Exporter.exe
echo   dist\GFL2-Packet-CSV-Exporter\windows-packet-capture.exe
echo.
echo The GUI stays non-elevated. Only windows-packet-capture.exe carries the administrator manifest.
if /i not "%~1"=="--no-pause" pause
exit /b 0

:fail
echo.
echo EXE build failed. See the messages above.
if /i not "%~1"=="--no-pause" pause
exit /b 1
