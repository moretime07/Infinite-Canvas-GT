@echo off
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

rem The full portable bundle includes a complete Python runtime. Validate each
rem candidate so a copied .venv from another computer cannot block startup.
set "PYEXE="
if exist "%~dp0python\python.exe" (
    "%~dp0python\python.exe" -c "import sys, struct; assert (3, 10) <= sys.version_info[:2] < (3, 14) and struct.calcsize('P') == 8" >nul 2>&1
    if not errorlevel 1 set "PYEXE=%~dp0python\python.exe"
)
if not defined PYEXE if exist "%~dp0.venv\Scripts\python.exe" (
    "%~dp0.venv\Scripts\python.exe" -c "import sys, struct; assert (3, 10) <= sys.version_info[:2] < (3, 14) and struct.calcsize('P') == 8" >nul 2>&1
    if not errorlevel 1 set "PYEXE=%~dp0.venv\Scripts\python.exe"
)
if not defined PYEXE (
    echo First run: installing the project environment...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\install_windows.ps1"
    if errorlevel 1 (
        echo Installation failed. Re-extract the complete portable ZIP, then run InfiniteCanvas.exe.
        pause
        exit /b 1
    )
    set "PYEXE=%~dp0.venv\Scripts\python.exe"
)

"%PYEXE%" -c "import sys, struct; assert (3, 10) <= sys.version_info[:2] < (3, 14) and struct.calcsize('P') == 8"
if errorlevel 1 (
    echo The selected Python is not a working 64-bit environment.
    pause
    exit /b 1
)

set "LAN_IP=127.0.0.1"
for /f "usebackq delims=" %%I in (`powershell -NoProfile -ExecutionPolicy Bypass -Command "$ip=(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.IPv4Address } | Select-Object -First 1 -ExpandProperty IPv4Address).IPAddress; if(-not $ip){$ip=(Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } | Sort-Object InterfaceMetric | Select-Object -First 1 -ExpandProperty IPAddress)}; if($ip){$ip}else{'127.0.0.1'}"`) do set "LAN_IP=%%I"
set "LOCAL_URL=http://127.0.0.1:3000/"
set "APP_URL=http://%LAN_IP%:3000/"

echo Starting Infinite-Canvas-GT...
echo Local: %LOCAL_URL%
echo LAN:   %APP_URL%
echo Press Ctrl+C to stop.
echo.

start /b cmd /c "timeout /t 3 /nobreak >nul && start %LOCAL_URL%"
"%PYEXE%" main.py

echo.
echo Server stopped.
pause
