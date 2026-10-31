@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title 照片归档工具 - Windows 首次安装

echo.
echo ====================================================
echo   照片归档与出勤统计 - 首次安装并打开网页
echo ====================================================
echo.

where py >nul 2>nul
if errorlevel 1 (
  echo [错误] 没有找到 Python 启动器 py。
  echo 请先安装 Python 3.11（64 位），安装时勾选：Add python.exe to PATH。
  echo 下载地址：https://www.python.org/downloads/release/python-3119/
  pause
  exit /b 1
)

py -3.11 -c "import sys" >nul 2>nul
if errorlevel 1 (
  echo [错误] 没有找到 Python 3.11。
  echo 请安装 Python 3.11（64 位）后再双击此文件。
  echo 下载地址：https://www.python.org/downloads/release/python-3119/
  pause
  exit /b 1
)

if not exist ".venv-win\Scripts\python.exe" (
  echo [1/3] 正在创建 Windows 虚拟环境...
  py -3.11 -m venv .venv-win
  if errorlevel 1 goto :failed
)

echo [2/3] 正在安装首次所需组件（可能需要几分钟）...
".venv-win\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed
".venv-win\Scripts\python.exe" -m pip install -e .
if errorlevel 1 goto :failed

if not exist "data\config.json" (
  echo 正在创建空白工作区“我的班”（后续可直接在网页输入自己的班级名）...
  ".venv-win\Scripts\photoorg.exe" init --class "我的班"
  if errorlevel 1 goto :failed
)

echo [3/3] 安装完成，即将打开本机网页...
call "%~dp0打开网页-Windows.bat"
exit /b %errorlevel%

:failed
echo.
echo [错误] 安装没有完成。请检查网络、Python 版本，或将本窗口内容截图后反馈。
pause
exit /b 1
