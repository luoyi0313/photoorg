@echo off
call "%~dp0make-transfer-zip.bat" %*
exit /b %ERRORLEVEL%
