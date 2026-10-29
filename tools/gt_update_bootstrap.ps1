param([Parameter(Mandatory=$true)][string]$AppDir)
$ErrorActionPreference = 'Stop'
$gtRoot = (Resolve-Path -LiteralPath $AppDir).Path
if (-not (Test-Path -LiteralPath (Join-Path $gtRoot 'main.py'))) { throw 'Put this tool in the installed project folder (beside main.py).' }
$gtPython = @((Join-Path $gtRoot '.venv\Scripts\python.exe'), (Join-Path $gtRoot 'python\python.exe')) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $gtPython) { throw 'Project Python is missing. Please run the dependency installer first.' }
$gtTemp = Join-Path ([IO.Path]::GetTempPath()) ('gt-updater-' + [guid]::NewGuid().ToString('N') + '.py')
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $gtHeaders = @{'User-Agent'='Infinite-Canvas-GT-Bootstrap'}
    $gtCommit = (Invoke-RestMethod -Headers $gtHeaders 'https://api.github.com/repos/moretime07/Infinite-Canvas-GT/commits/main').sha
    if ($gtCommit -notmatch '^[0-9a-f]{40}$') { throw 'Invalid GitHub revision.' }
    $gtTree = Invoke-RestMethod -Headers $gtHeaders ("https://api.github.com/repos/moretime07/Infinite-Canvas-GT/git/trees/$gtCommit`?recursive=1")
    $gtEntry = $gtTree.tree | Where-Object { $_.path -eq 'project_updater.py' -and $_.mode -eq '100644' } | Select-Object -First 1
    if (-not $gtEntry -or $gtTree.truncated) { throw 'The GT release is incomplete.' }
    Invoke-WebRequest -UseBasicParsing -Headers $gtHeaders ("https://raw.githubusercontent.com/moretime07/Infinite-Canvas-GT/$gtCommit/project_updater.py") -OutFile $gtTemp
    $gtBytes = [IO.File]::ReadAllBytes($gtTemp)
    $gtHeader = [Text.Encoding]::ASCII.GetBytes(('blob ' + $gtBytes.Length + [char]0))
    $gtHash = [Security.Cryptography.SHA1]::Create()
    try { $gtDigest = [BitConverter]::ToString($gtHash.ComputeHash([byte[]]($gtHeader + $gtBytes))).Replace('-','').ToLowerInvariant() } finally { $gtHash.Dispose() }
    if ($gtDigest -ne $gtEntry.sha) { throw 'Updater integrity check failed.' }
    & $gtPython $gtTemp --app-dir $gtRoot
    if ($LASTEXITCODE -ne 0) { throw 'Update did not complete. Stop the old server and check the messages above.' }
    Start-Process -FilePath $gtPython -ArgumentList @('main.py') -WorkingDirectory $gtRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $gtRoot '_gt-bootstrap.log') -RedirectStandardError (Join-Path $gtRoot '_gt-bootstrap-error.log')
    Start-Sleep -Seconds 3
    Start-Process 'http://127.0.0.1:3000/'
} finally {
    if (Test-Path -LiteralPath $gtTemp) { Remove-Item -LiteralPath $gtTemp -Force }
}
