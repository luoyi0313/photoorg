@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title photoorg - build Windows portable

rem Windows has a 260-character path limit; the onnx package contains very
rem deep test-data paths. If the project path is long, build from a short copy.
if defined PHOTOORG_SHORT_PATH goto after_path_check
set "PHOTOORG_CUR=%CD%"
set "PHOTOORG_LEN=0"
:count_path
if not defined PHOTOORG_CUR goto count_done
set "PHOTOORG_CUR=%PHOTOORG_CUR:~1%"
set /a PHOTOORG_LEN+=1
goto count_path
:count_done
if %PHOTOORG_LEN% GTR 100 (
  echo Current path is %PHOTOORG_LEN% characters long, which can break pip/onnx.
  echo Switching to short-path-build.bat ...
  echo.
  call "%~dp0short-path-build.bat" %*
  exit /b %ERRORLEVEL%
)
:after_path_check

echo.
echo ====================================================
echo   photoorg - build Windows portable
echo ====================================================
echo.
echo Working folder: %CD%
echo If this path is very long, close this and run short-path-build.bat instead.
echo.

set "PYRUN="
where py >nul 2>nul
if not errorlevel 1 (
  py -3.11 -c "import sys" >nul 2>nul
  if not errorlevel 1 set "PYRUN=py -3.11"
)
if not defined PYRUN (
  if exist "%LocalAppData%\Programs\Python\Python311\python.exe" (
    set PYRUN="%LocalAppData%\Programs\Python\Python311\python.exe"
  )
)
if not defined PYRUN (
  where python >nul 2>nul
  if not errorlevel 1 (
    python -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,11) else 1)" >nul 2>nul
    if not errorlevel 1 set "PYRUN=python"
  )
)
if not defined PYRUN goto :no_python

%PYRUN% -c "import struct,sys; sys.exit(0 if struct.calcsize('P')==8 else 1)" >nul 2>nul
if errorlevel 1 goto :no_64bit

if not exist ".venv-build\Scripts\python.exe" (
  echo [1/4] Creating virtual environment .venv-build ...
  %PYRUN% -m venv .venv-build
  if errorlevel 1 goto :failed
)

echo [2/4] Installing build dependencies (first run may take several minutes)...
".venv-build\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".venv-build\Scripts\python.exe" -m pip install -e ".[build]"
if errorlevel 1 goto :failed

echo.
echo [3/4] Building portable app (uses offline model if available)...
".venv-build\Scripts\python.exe" "packaging\build_windows.py" %*
if errorlevel 1 goto :failed

echo.
echo [4/4] Done. Output is in the dist folder.
if exist "dist" start "" "dist"
echo.
echo Copy the generated zip from the dist folder to the target computer.
echo Extract it and double-click the application exe.
echo.
pause
exit /b 0

:no_python
echo [ERROR] Python 3.11 (64-bit) was not found.
echo.
if exist "windows-offline\python-3.11.9-amd64.exe" (
  echo The project includes an offline installer.
  echo Please run install-python.bat first.
) else (
  echo Install Python 3.11 (64-bit) and enable "Add python.exe to PATH".
  echo Download: https://www.python.org/downloads/release/python-3119/
)
echo.
pause
exit /b 1

:no_64bit
echo [ERROR] The installed Python 3.11 is 32-bit. 64-bit is required.
echo Uninstall it and install 64-bit Python 3.11.
echo.
pause
exit /b 1

:failed
echo.
echo [ERROR] Build failed. Please screenshot the messages above.
echo Common causes: long path (WinError 206), disk space, antivirus, pip network error.
echo If the path is long, run short-path-build.bat instead.
echo.
pause
exit /b 1
