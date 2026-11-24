@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" --update
set "update_exit_code=%errorlevel%"
echo.
pause
exit /b %update_exit_code%
