@echo off
setlocal EnableExtensions
chcp 65001 >nul
title photoorg - short path build

echo.
echo ====================================================
echo   photoorg - build from a short path
echo ====================================================
echo.
echo The current folder path may be too long for Windows/pip.
echo This script copies the project to %%USERPROFILE%%\photoorg-build and builds there.
echo.

set "SRC=%~dp0"
set "DST=%USERPROFILE%\photoorg-build"
if exist "%DST%\" set "DST=%USERPROFILE%\photoorg-build-2"
if exist "%DST%\" set "DST=%USERPROFILE%\photoorg-build-3"
if exist "%DST%\" (
  echo [ERROR] photoorg-build, -2 and -3 already exist in your profile.
  echo Please delete or rename one of them and run again.
  echo.
  pause
  exit /b 1
)

if /I "%SRC%"=="%DST%\" (
  echo [ERROR] The source and target are the same folder.
  pause
  exit /b 1
)

echo Copying to: %DST%
echo (about 210 MB, please wait...)
echo.
robocopy "%SRC%" "%DST%" /E /R:0 /W:0 /NFL /NDL /NJH /NJS /NP ^
  /XD ".venv" ".venv-win" ".venv-build" ".git" "data" "photos" "roster" "out" "dist" "build" "__pycache__" "photoorg.egg-info" ^
  /XF "photoorg-windows-build.zip"
if errorlevel 8 (
  echo.
  echo [ERROR] Copy failed. Please check disk space and permissions.
  echo.
  pause
  exit /b 1
)

if not exist "%DST%\build-windows.bat" (
  echo.
  echo [ERROR] Copy incomplete: build-windows.bat not found in %DST%
  echo.
  pause
  exit /b 1
)

echo Copy finished. Starting build from: %DST%
echo.
cd /d "%DST%"
set "PHOTOORG_SHORT_PATH=1"
call "%DST%\build-windows.bat" %*
set "RC=%ERRORLEVEL%"

echo.
echo Build exit code: %RC%
echo Output folder: %DST%\dist
echo.
exit /b %RC%
