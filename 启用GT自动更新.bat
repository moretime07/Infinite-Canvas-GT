@echo off
setlocal
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
set "GT_UPDATE_ROOT=%~dp0"
echo This tool switches this installation to Infinite-Canvas-GT updates.
echo Close the old server with Ctrl+C first. Personal data will be kept.
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $gtBootstrap=Join-Path ([IO.Path]::GetTempPath()) ('gt-bootstrap-'+[guid]::NewGuid().ToString('N')+'.ps1'); try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/moretime07/Infinite-Canvas-GT/main/tools/gt_update_bootstrap.ps1' -OutFile $gtBootstrap; & $gtBootstrap -AppDir $env:GT_UPDATE_ROOT; if (-not $?) { exit 1 } } catch { Write-Host $_.Exception.Message; exit 1 } finally { if (Test-Path -LiteralPath $gtBootstrap) { Remove-Item -LiteralPath $gtBootstrap -Force } }"
set "GT_UPDATE_EXIT=%ERRORLEVEL%"
if not "%GT_UPDATE_EXIT%"=="0" (
    echo Update did not finish. Check the message above and retry.
) else (
    echo GT update enabled. Future updates are available in the web sidebar.
)
pause
exit /b %GT_UPDATE_EXIT%
