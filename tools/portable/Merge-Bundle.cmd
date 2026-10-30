@echo off
setlocal
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0Merge-Bundle.ps1"
set "bundle_result=%errorlevel%"
echo.
if not "%bundle_result%"=="0" echo Merge failed. Read the message above. Original parts were not removed.
pause
exit /b %bundle_result%
