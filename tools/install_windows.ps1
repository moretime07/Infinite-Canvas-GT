[CmdletBinding()]
param(
    [string]$PythonExecutable = '',
    [switch]$InstallPython,
    [switch]$NonInteractive,
    [ValidateSet('auto', 'pypi', 'tuna', 'configured')][string]$PackageSource = 'auto',
    [string]$IndexUrl = '',
    [string]$ProxyUrl = '',
    [string]$CaBundle = ''
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
$appRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $appRoot '.venv\Scripts\python.exe'
$versionProbe = "import sys, struct; assert (3, 10) <= sys.version_info[:2] < (3, 14) and struct.calcsize('P') == 8; print(sys.executable)"

function Test-AppPython([string]$Executable) {
    if (-not $Executable -or -not (Test-Path -LiteralPath $Executable -PathType Leaf)) { return $false }
    try {
        & $Executable -c $versionProbe 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
}

function Find-AppPython {
    $candidates = @()
    $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($launcher) {
        foreach ($version in @('-3.11', '-3.12', '-3.13', '-3.10')) {
            try {
                $detected = & $launcher.Source $version -c $versionProbe 2>$null
                if ($LASTEXITCODE -eq 0 -and $detected) { $candidates += [string]$detected }
            } catch { }
        }
    }
    foreach ($version in @('311', '312', '313', '310')) {
        $candidates += Join-Path $env:LOCALAPPDATA "Programs\Python\Python$version\python.exe"
    }
    $onPath = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($onPath -and $onPath.Source -notlike '*\WindowsApps\*') { $candidates += $onPath.Source }
    foreach ($candidate in $candidates) {
        if (Test-AppPython $candidate) { return $candidate }
    }
    return ''
}

Push-Location -LiteralPath $appRoot
try {
    if (Test-Path -LiteralPath (Join-Path $appRoot '.venv')) {
        if (-not (Test-AppPython $venvPython)) {
            throw 'The existing .venv is not a working 64-bit Python 3.10-3.13 environment. Rename it to keep a backup, then rerun this installer.'
        }
    } else {
        if ($PythonExecutable) {
            if (-not (Test-AppPython $PythonExecutable)) { throw 'The specified PythonExecutable is not a supported 64-bit Python.' }
            $selectedPython = $PythonExecutable
        } else {
            $selectedPython = Find-AppPython
        }
        if (-not $selectedPython) {
            $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
            if (-not $InstallPython -and -not $NonInteractive -and $winget) {
                $answer = Read-Host 'Python is missing. Download and install Python 3.11 via WinGet? [Y/N]'
                $InstallPython = $answer -match '^(?i)y(es)?$'
            }
            if (-not $InstallPython -or -not $winget) {
                throw 'Install 64-bit Python 3.11 from https://www.python.org/downloads/windows/ and rerun this script. With WinGet, you can also pass -InstallPython.'
            }
            & $winget.Source install --exact --id Python.Python.3.11 --source winget --scope user --accept-package-agreements --accept-source-agreements
            if ($LASTEXITCODE -ne 0) { throw 'WinGet could not install Python. Please use the official Python installer.' }
            $selectedPython = Find-AppPython
            if (-not $selectedPython) { throw 'Python was installed but not found. Reopen this installer or pass -PythonExecutable with its full path.' }
        }
        Write-Host "Creating a project-only virtual environment with $selectedPython"
        & $selectedPython -m venv (Join-Path $appRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create .venv. Install Python with its pip and venv components enabled.' }
    }
    Write-Host 'Installing base dependencies into .venv (not into system Python)...'
    $dependencyArgs = @('-X', 'utf8', (Join-Path $PSScriptRoot 'install_dependencies.py'), '--requirements', (Join-Path $appRoot 'requirements.txt'), '--source', $PackageSource)
    if ($IndexUrl) { $dependencyArgs += @('--index-url', $IndexUrl) }
    if ($ProxyUrl) { $dependencyArgs += @('--proxy', $ProxyUrl) }
    if ($CaBundle) { $dependencyArgs += @('--ca-bundle', $CaBundle) }
    & $venvPython @dependencyArgs
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed. See the safe error summary above. You can select -PackageSource tuna or supply -ProxyUrl / -CaBundle, then rerun.' }
    & $venvPython -c "import main; assert main.app; print('Application import: OK')"
    if ($LASTEXITCODE -ne 0) { throw 'Application startup check failed. Keep the error output for troubleshooting.' }
    Write-Host ''
    Write-Host 'Installation complete. Double-click the startup BAT file in the project root.' -ForegroundColor Green
    Write-Host 'Browser address: http://127.0.0.1:3000/'
    Write-Host 'Enter your own provider API key in the web settings. No private configuration was downloaded.'
    Write-Host 'Local GPU motion extraction is optional and has a separate installer.'
} catch {
    Write-Host "Installation failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
} finally {
    Pop-Location
}
