@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0start.ps1" %*
set "launch_exit_code=%errorlevel%"
echo.
pause
exit /b %launch_exit_code%
