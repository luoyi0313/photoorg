@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title 照片归档工具 - 本机网页

if not exist ".venv-win\Scripts\python.exe" (
  echo 还没有完成首次安装。
  echo 请先双击：首次安装并打开网页-Windows.bat
  pause
  exit /b 1
)

set PORT=8765
for /f "tokens=5" %%P in ('netstat -ano ^| findstr /r /c:":%PORT% .*LISTENING"') do (
  echo 正在停止旧的网页服务（端口 %PORT%）...
  taskkill /F /PID %%P >nul 2>nul
)

set URL=http://127.0.0.1:%PORT%/?v=%RANDOM%%RANDOM%
if not exist "data\config.json" (
  echo 正在创建空白工作区“我的班”...
  ".venv-win\Scripts\photoorg.exe" init --class "我的班"
  if errorlevel 1 goto :failed
)
echo.
echo 正在启动最新本机网页服务...
echo 浏览器地址：%URL%
echo 关闭本窗口或按 Ctrl+C 可停止服务。
echo.

start "" "%URL%"
".venv-win\Scripts\photoorg.exe" review --port %PORT% --no-browser

if errorlevel 1 (
  echo.
  echo 网页服务异常退出。请将上面的错误信息截图后反馈。
  pause
)
