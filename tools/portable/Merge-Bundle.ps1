# Local-only release merger. Does not download, install or delete anything.
# Windows PowerShell 5.1 compatible; ASCII source avoids code-page problems.
$ErrorActionPreference = 'Stop'
function Get-BundleHash([string]$FilePath) {
    $stream = [IO.File]::OpenRead($FilePath)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return ([BitConverter]::ToString($hasher.ComputeHash($stream))).Replace('-', '').ToLowerInvariant() }
    finally { $hasher.Dispose(); $stream.Dispose() }
}
try {
    $bundleRoot = [IO.Path]::GetFullPath($PSScriptRoot)
    $manifest = Get-Content -LiteralPath (Join-Path $bundleRoot 'bundle.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    $expectedNames = @('InfiniteCanvas-GT-Full.zip.001', 'InfiniteCanvas-GT-Full.zip.002')
    if ($manifest.format -ne 1 -or $manifest.name -cne 'InfiniteCanvas-GT-Full.zip' -or
        $manifest.sha256 -notmatch '^[a-f0-9]{64}$' -or @($manifest.parts).Count -ne 2) {
        throw 'Invalid bundle manifest. Download all files again from the official release.'
    }
    $totalBytes = [long]0
    for ($i = 0; $i -lt 2; $i++) {
        $part = $manifest.parts[$i]
        if ($part.name -cne $expectedNames[$i] -or $part.sha256 -notmatch '^[a-f0-9]{64}$' -or
            [long]$part.bytes -le 0 -or [long]$part.bytes -ge 2147483648) {
            throw 'Invalid part metadata.'
        }
        $partPath = Join-Path $bundleRoot $expectedNames[$i]
        if (!(Test-Path -LiteralPath $partPath -PathType Leaf)) { throw ('Missing part: ' + $expectedNames[$i]) }
        Write-Host ('Verifying part ' + ($i + 1) + '/2...')
        if ((Get-Item -LiteralPath $partPath).Length -ne [long]$part.bytes -or
            (Get-BundleHash $partPath) -ne $part.sha256) {
            throw ('Part is incomplete or damaged: ' + $expectedNames[$i])
        }
        $totalBytes += [long]$part.bytes
    }
    if ($totalBytes -ne [long]$manifest.bytes) { throw 'Bundle size does not match the manifest.' }
    $destination = Join-Path $bundleRoot 'InfiniteCanvas-GT-Full.zip'
    if (Test-Path -LiteralPath $destination) {
        if ((Get-BundleHash $destination) -eq $manifest.sha256) {
            Write-Host 'The verified ZIP already exists. Extract the entire ZIP, then run InfiniteCanvas.exe.'
            exit 0
        }
        throw 'A different ZIP already exists. Move it elsewhere first; it will not be overwritten.'
    }
    $temporary = Join-Path $bundleRoot ('InfiniteCanvas-GT-Full.zip.merging-' + [guid]::NewGuid().ToString('N'))
    # Names above are fixed, so all writes stay in this release download folder.
    $writer = [IO.File]::Open($temporary, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
    try {
        foreach ($name in $expectedNames) {
            $reader = [IO.File]::OpenRead((Join-Path $bundleRoot $name))
            try { $reader.CopyTo($writer, 4194304) } finally { $reader.Dispose() }
            Write-Host ('Merged: ' + $name)
        }
    } finally { $writer.Dispose() }
    if ((Get-BundleHash $temporary) -ne $manifest.sha256) {
        throw 'Merged ZIP verification failed. Partial output was preserved; original parts are unchanged.'
    }
    Move-Item -LiteralPath $temporary -Destination $destination
    Write-Host 'SUCCESS: InfiniteCanvas-GT-Full.zip'
    Write-Host 'Extract the entire ZIP to a writable folder (for example D:\InfiniteCanvas).'
    Write-Host 'Then double-click InfiniteCanvas.exe and configure YOUR OWN API key.'
} catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
