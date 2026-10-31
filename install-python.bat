@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title photoorg - install Python 3.11

echo.
echo ====================================================
echo   Install Python 3.11 (64-bit) for building
echo ====================================================
echo.

py -3.11 -c "import sys" >nul 2>nul
if not errorlevel 1 (
  py -3.11 -c "import struct,sys; sys.exit(0 if struct.calcsize('P')==8 else 1)" >nul 2>nul
  if errorlevel 1 (
    echo [ERROR] The installed Python 3.11 is 32-bit.
    echo Uninstall it and run this script again.
    echo.
    pause
    exit /b 1
  )
  echo [OK] Python 3.11 (64-bit) is already installed.
  echo Next: run build-windows.bat
  echo.
  pause
  exit /b 0
)

if not exist "windows-offline\python-3.11.9-amd64.exe" (
  echo [ERROR] Installer not found:
  echo   windows-offline\python-3.11.9-amd64.exe
  echo.
  echo Copy the whole project folder, including windows-offline.
  pause
  exit /b 1
)

echo Installing Python 3.11 for the current user (no admin required)...
echo This may take about one minute. No progress bar is normal.
echo.
start /wait "" "windows-offline\python-3.11.9-amd64.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 Include_launcher=1 Include_pip=1
if errorlevel 1 (
  echo.
  echo [ERROR] Installer returned an error.
  echo Try running it manually:
  echo   windows-offline\python-3.11.9-amd64.exe
  echo Check: Add python.exe to PATH
  echo.
  pause
  exit /b 1
)

set "PYDIR=%LocalAppData%\Programs\Python\Python311"
if exist "%PYDIR%\python.exe" (
  echo.
  echo Installed:
  "%PYDIR%\python.exe" -c "import sys; print(sys.version)"
  "%PYDIR%\python.exe" -c "import struct; print('pointer bytes:', struct.calcsize('P'))"
) else (
  echo.
  echo Installed, but python.exe was not found in the default folder.
)

echo.
echo Close this window, then run build-windows.bat
echo (The new window will pick up the updated PATH.)
echo.
pause
exit /b 0
