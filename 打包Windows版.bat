@echo off
call "%~dp0build-windows.bat" %*
exit /b %ERRORLEVEL%
