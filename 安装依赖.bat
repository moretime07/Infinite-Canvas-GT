@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install_windows.ps1" %*
set "INSTALL_EXIT=%ERRORLEVEL%"
echo.
if not "%INSTALL_EXIT%"=="0" echo Installation failed. Please read the error above.
if /i not "%~1"=="-NonInteractive" pause
exit /b %INSTALL_EXIT%
