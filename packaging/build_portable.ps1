[CmdletBinding()]
param(
    [switch]$SkipRuntime,
    [switch]$SkipNpmInstall,
    [switch]$IncludeInstaller
)

$ErrorActionPreference = 'Stop'
if ($IncludeInstaller) {
    throw 'The bundled runtime is too large for a reliable Squirrel installer. Build the portable ZIP, then run build_self_extract.ps1.'
}
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$desktopRoot = Join-Path $projectRoot 'desktop'
$backendStage = Join-Path $projectRoot '.package\backend'
$buildTemp = Join-Path $projectRoot '.package\electron-temp'

if (-not $SkipRuntime) {
    & (Join-Path $PSScriptRoot 'build_runtime.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Python runtime build failed.' }
}
$runtimePython = Join-Path $projectRoot '.runtime\python\python.exe'
if (-not (Test-Path -LiteralPath $runtimePython)) {
    throw 'Portable Python runtime is missing.'
}
& $runtimePython (Join-Path $PSScriptRoot 'verify_runtime.py') --project-root $projectRoot
if ($LASTEXITCODE -ne 0) { throw 'Existing Python runtime verification failed.' }
& $runtimePython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Existing Python runtime dependency check failed.' }

$expectedStage = [System.IO.Path]::GetFullPath((Join-Path $projectRoot '.package\backend'))
$actualStage = [System.IO.Path]::GetFullPath($backendStage)
if ($actualStage -ne $expectedStage -or -not $actualStage.StartsWith(
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot '.package')),
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Refusing to clear unexpected staging path: $actualStage"
}
if (Test-Path -LiteralPath $actualStage) {
    Remove-Item -LiteralPath $actualStage -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $actualStage | Out-Null
$expectedTemp = [System.IO.Path]::GetFullPath((Join-Path $projectRoot '.package\electron-temp'))
$actualTemp = [System.IO.Path]::GetFullPath($buildTemp)
if ($actualTemp -ne $expectedTemp -or -not $actualTemp.StartsWith(
    [System.IO.Path]::GetFullPath((Join-Path $projectRoot '.package')),
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Refusing to clear unexpected build temp path: $actualTemp"
}
if (Test-Path -LiteralPath $actualTemp) {
    Remove-Item -LiteralPath $actualTemp -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $actualTemp | Out-Null
foreach ($directory in @('breeze_infer', 'configs', 't8_runtime', 'manifests')) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $directory) -Destination $backendStage -Recurse -Force
}
$modelsStage = Join-Path $backendStage 'models'
New-Item -ItemType Directory -Force -Path $modelsStage | Out-Null
Get-ChildItem -LiteralPath (Join-Path $projectRoot 'models') -Force |
    Where-Object { $_.Name -ne 'Breeze-TTS-2' } |
    ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $modelsStage -Recurse -Force }
if (Test-Path -LiteralPath (Join-Path $modelsStage 'Breeze-TTS-2')) {
    throw 'Model weights must not be embedded in the portable package staging directory.'
}
New-Item -ItemType Directory -Force -Path (Join-Path $backendStage 'desktop') | Out-Null
Copy-Item -LiteralPath (Join-Path $desktopRoot 'src') -Destination (Join-Path $backendStage 'desktop') -Recurse -Force
foreach ($file in @(
    'MODEL_LICENSE',
    'LICENSE',
    'NOTICE',
    'THIRD_PARTY_NOTICES.md',
    'requirements-desktop.lock.txt',
    'requirements-whisper.txt',
    'T8_DISTRIBUTION.md'
)) {
    Copy-Item -LiteralPath (Join-Path $projectRoot $file) -Destination $backendStage -Force
}

$desktopManifest = Get-Content -LiteralPath (Join-Path $desktopRoot 'package.json') -Raw | ConvertFrom-Json
$packageBaseName = "T8star-Aix-Voice-Studio-v$($desktopManifest.version)"
$generatedTargets = @(
    (Join-Path $desktopRoot "out\$packageBaseName-win32-x64"),
    (Join-Path $desktopRoot "out\make\zip\win32\x64\$packageBaseName-win32-x64-$($desktopManifest.version).zip")
)
$desktopOutRoot = [System.IO.Path]::GetFullPath((Join-Path $desktopRoot 'out'))
foreach ($generatedTarget in $generatedTargets) {
    $resolvedTarget = [System.IO.Path]::GetFullPath($generatedTarget)
    if (-not $resolvedTarget.StartsWith(
        $desktopOutRoot + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing to clear unexpected generated target: $resolvedTarget"
    }
    if (Test-Path -LiteralPath $resolvedTarget) {
        Remove-Item -LiteralPath $resolvedTarget -Recurse -Force
    }
}

$previousTemp = $env:TEMP
$previousTmp = $env:TMP
$previousElectronZipDir = $env:T8_ELECTRON_ZIP_DIR
$electronVersion = [string]$desktopManifest.devDependencies.electron
$electronZipName = "electron-v$electronVersion-win32-x64.zip"
$electronChecksumManifest = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'electron-checksums.json') -Raw |
    ConvertFrom-Json
$versionEntry = $electronChecksumManifest.PSObject.Properties[$electronVersion]
if (-not $versionEntry -or -not $versionEntry.Value.'win32-x64') {
    throw "No trusted Electron checksum is pinned for $electronVersion win32-x64."
}
$electronChecksum = $versionEntry.Value.'win32-x64'
if ([string]$electronChecksum.file -ne $electronZipName -or
    [string]$electronChecksum.sha256 -notmatch '^[0-9a-fA-F]{64}$') {
    throw "Invalid Electron checksum entry for $electronVersion win32-x64."
}

function Test-TrustedElectronArchive([string]$ArchivePath) {
    if (-not (Test-Path -LiteralPath $ArchivePath -PathType Leaf)) { return $false }
    $actualHash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    return $actualHash -eq ([string]$electronChecksum.sha256).ToLowerInvariant()
}

if ($env:T8_ELECTRON_ZIP_DIR) {
    $explicitElectronZip = Join-Path ([System.IO.Path]::GetFullPath($env:T8_ELECTRON_ZIP_DIR)) $electronZipName
    if (-not (Test-TrustedElectronArchive $explicitElectronZip)) {
        throw "T8_ELECTRON_ZIP_DIR does not contain the trusted Electron archive: $electronZipName"
    }
    Write-Host "Using verified Electron archive: $explicitElectronZip"
}
if (-not $env:T8_ELECTRON_ZIP_DIR) {
    $electronCacheRoot = Join-Path $env:LOCALAPPDATA 'electron\Cache'
    if (Test-Path -LiteralPath $electronCacheRoot) {
        $cachedElectronZip = Get-ChildItem -LiteralPath $electronCacheRoot -Recurse -File -Filter $electronZipName |
            Select-Object -First 1
        if ($cachedElectronZip -and (Test-TrustedElectronArchive $cachedElectronZip.FullName)) {
            $env:T8_ELECTRON_ZIP_DIR = $cachedElectronZip.DirectoryName
            Write-Host "Using verified cached Electron archive: $($cachedElectronZip.FullName)"
        } elseif ($cachedElectronZip) {
            Write-Warning "Ignoring cached Electron archive with a checksum mismatch: $($cachedElectronZip.FullName)"
        }
    }
}
$env:TEMP = $actualTemp
$env:TMP = $actualTemp
Push-Location $desktopRoot
try {
    if (-not $SkipNpmInstall) {
        if (Test-Path -LiteralPath (Join-Path $desktopRoot 'package-lock.json')) {
            npm ci
        } else {
            npm install
        }
        if ($LASTEXITCODE -ne 0) { throw 'npm dependency installation failed.' }
    }
    npm audit --omit=dev
    if ($LASTEXITCODE -ne 0) { throw 'Production npm dependency audit failed.' }
    npm test
    if ($LASTEXITCODE -ne 0) { throw 'Desktop regression tests failed.' }
    npm run make:portable
    if ($LASTEXITCODE -ne 0) { throw 'Electron package build failed.' }
    npm run verify
    if ($LASTEXITCODE -ne 0) { throw 'Packaged application verification failed.' }
} finally {
    Pop-Location
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    $env:T8_ELECTRON_ZIP_DIR = $previousElectronZipDir
}

$artifactPath = $generatedTargets[1]
if (-not (Test-Path -LiteralPath $artifactPath -PathType Leaf)) {
    throw "Portable zip artifact was not produced: $artifactPath"
}
$artifacts = @(Get-Item -LiteralPath $artifactPath)
$lines = foreach ($artifact in $artifacts) {
    $hash = (Get-FileHash -LiteralPath $artifact.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $($artifact.Name)"
}
$checksumPath = Join-Path $desktopRoot 'out\make\SHA256SUMS.txt'
[System.IO.File]::WriteAllLines($checksumPath, $lines, [System.Text.UTF8Encoding]::new($false))
Write-Host "Portable artifact complete. Checksums: $checksumPath"
