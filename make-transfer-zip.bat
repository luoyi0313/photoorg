@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title photoorg - make transfer zip

echo.
echo ====================================================
echo   photoorg - make Windows transfer zip
echo ====================================================
echo.

if exist ".venv-build\Scripts\python.exe" (
  ".venv-build\Scripts\python.exe" "packaging\make_transfer_zip.py" %*
  if not errorlevel 1 goto :done
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3.11 "packaging\make_transfer_zip.py" %*
  if not errorlevel 1 goto :done
)

where python >nul 2>nul
if not errorlevel 1 (
  python "packaging\make_transfer_zip.py" %*
  if not errorlevel 1 goto :done
)

echo [ERROR] Python 3.11 was not found.
echo Run install-python.bat first, or install Python 3.11 (64-bit).
echo.
pause
exit /b 1

:done
set "RC=%ERRORLEVEL%"
echo.
echo Done. You can copy photoorg-windows-build.zip to Windows.
echo.
pause
exit /b %RC%
