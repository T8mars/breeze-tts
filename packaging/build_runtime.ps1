[CmdletBinding()]
param(
    [string]$RuntimeRoot = (Join-Path $PSScriptRoot '..\.runtime\python'),
    [string]$SourcePythonRoot = $env:T8_SOURCE_PYTHON_ROOT,
    [switch]$SkipInstall
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimePath = [System.IO.Path]::GetFullPath($RuntimeRoot)
$sourcePath = if ($SourcePythonRoot) {
    [System.IO.Path]::GetFullPath($SourcePythonRoot)
} else {
    $null
}
$lockPath = Join-Path $projectRoot 'requirements-desktop.lock.txt'
$whisperModelRoot = Join-Path $projectRoot '.runtime\whisper-models\faster-whisper-small'

if (-not (Test-Path -LiteralPath (Join-Path $runtimePath 'python.exe'))) {
    if (-not $sourcePath -or -not (Test-Path -LiteralPath (Join-Path $sourcePath 'python.exe'))) {
        throw "Portable CPython seed not found. Pass -SourcePythonRoot (or set T8_SOURCE_PYTHON_ROOT) to a clean CPython 3.10 x64 distribution."
    }
    New-Item -ItemType Directory -Force -Path $runtimePath | Out-Null
    Get-ChildItem -LiteralPath $sourcePath -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $runtimePath -Recurse -Force
    }
}

$python = Join-Path $runtimePath 'python.exe'
$version = & $python -c 'import platform,sys; print(platform.architecture()[0]); print(sys.version_info[:3])'
if ($LASTEXITCODE -ne 0 -or $version[0] -ne '64bit' -or $version[1] -notmatch '^\(3, 10,') {
    throw "Runtime must be 64-bit CPython 3.10. Found: $($version -join ' ')"
}

if (-not $SkipInstall) {
    & $python -m pip install --break-system-packages --disable-pip-version-check --upgrade 'pip==26.1.2' 'setuptools==82.0.1' 'wheel==0.46.3'
    if ($LASTEXITCODE -ne 0) { throw 'Failed to prepare pip.' }
    if (Test-Path -LiteralPath $lockPath) {
        & $python -m pip install --break-system-packages --disable-pip-version-check --index-url 'https://download.pytorch.org/whl/cu128' --extra-index-url 'https://pypi.org/simple' -r $lockPath
        if ($LASTEXITCODE -ne 0) { throw 'Failed to install the locked desktop runtime.' }
    } else {
        & $python -m pip install --break-system-packages --disable-pip-version-check --index-url 'https://download.pytorch.org/whl/cu128' --extra-index-url 'https://pypi.org/simple' 'torch==2.9.1' 'torchaudio==2.9.1'
        if ($LASTEXITCODE -ne 0) { throw 'Failed to install PyTorch cu128 runtime.' }
        & $python -m pip install --break-system-packages --disable-pip-version-check -r (Join-Path $projectRoot 'requirements-desktop.in')
        if ($LASTEXITCODE -ne 0) { throw 'Failed to install Breeze desktop dependencies.' }
    }
    & $python -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Python dependency consistency check failed.' }
}

& $python (Join-Path $PSScriptRoot 'verify_runtime.py') --project-root $projectRoot
if ($LASTEXITCODE -ne 0) { throw 'Runtime verification failed.' }

& $python (Join-Path $PSScriptRoot 'download_whisper_model.py') --output-dir $whisperModelRoot
if ($LASTEXITCODE -ne 0) { throw 'Bundled Whisper Small model download failed.' }

$freeze = & $python -m pip freeze --all | ForEach-Object {
    if ($_ -match '^pip @ ') { 'pip==26.1.2' }
    elseif ($_ -match '^setuptools @ ') { 'setuptools==82.0.1' }
    else { $_ }
}
[System.IO.File]::WriteAllLines($lockPath, $freeze, [System.Text.UTF8Encoding]::new($false))
Write-Host "Portable runtime ready: $runtimePath"
Write-Host "Bundled Whisper Small ready: $whisperModelRoot"
Write-Host "Resolved lock written: $lockPath"
