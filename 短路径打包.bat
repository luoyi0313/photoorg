@echo off
call "%~dp0short-path-build.bat" %*
exit /b %ERRORLEVEL%
