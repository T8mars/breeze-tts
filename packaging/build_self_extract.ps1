[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$desktopRoot = Join-Path $projectRoot 'desktop'
$package = Get-Content -LiteralPath (Join-Path $desktopRoot 'package.json') -Raw | ConvertFrom-Json
$packageBaseName = "T8star-Aix-Voice-Studio-v$($package.version)"
$packageDirectory = [System.IO.Path]::GetFullPath(
    (Join-Path $desktopRoot "out\$packageBaseName-win32-x64")
)
$makeRoot = [System.IO.Path]::GetFullPath((Join-Path $desktopRoot 'out\make'))
$outputDirectory = [System.IO.Path]::GetFullPath(
    (Join-Path $makeRoot 'self-extract\win32\x64')
)
$makePrefix = $makeRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) +
    [System.IO.Path]::DirectorySeparatorChar
if (-not $outputDirectory.StartsWith($makePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing self-extract output outside make directory: $outputDirectory"
}
if (-not (Test-Path -LiteralPath (Join-Path $packageDirectory 'T8star-Aix-Voice-Studio.exe'))) {
    throw "Packaged desktop application is missing: $packageDirectory"
}

$sevenZipCandidates = @(
    (Get-Command 7z.exe -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source -First 1),
    (Join-Path $env:ProgramFiles '7-Zip\7z.exe')
)
if (${env:ProgramFiles(x86)}) {
    $sevenZipCandidates += Join-Path ${env:ProgramFiles(x86)} '7-Zip\7z.exe'
}
$sevenZip = $sevenZipCandidates | Where-Object {
    $_ -and (Test-Path -LiteralPath $_ -PathType Leaf)
} | Select-Object -First 1
if (-not $sevenZip) {
    throw '7-Zip 25.x or newer is required to build the large self-extracting Windows package.'
}
$versionBanner = (& $sevenZip 2>&1 |
    ForEach-Object { $_.ToString().Trim() } |
    Where-Object { $_ -match '^7-Zip\s+\d+' } |
    Select-Object -First 1) -join ''
if ($versionBanner -notmatch '^7-Zip\s+(\d+)(?:\.(\d+))?') {
    throw "Unable to determine 7-Zip version from: $versionBanner"
}
if ([int]$Matches[1] -lt 25) {
    throw "7-Zip 25.x or newer is required; found $versionBanner"
}
$sfxModule = Join-Path (Split-Path -Parent $sevenZip) '7z.sfx'
if (-not (Test-Path -LiteralPath $sfxModule -PathType Leaf)) {
    throw "7-Zip SFX module is missing: $sfxModule"
}

New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null
$payload = Join-Path $outputDirectory "$packageBaseName-win32-x64-$($package.version).7z"
$selfExtract = Join-Path $outputDirectory "$packageBaseName-SelfExtract.exe"
foreach ($target in @($payload, $selfExtract)) {
    $fullTarget = [System.IO.Path]::GetFullPath($target)
    if (-not $fullTarget.StartsWith(
        $outputDirectory + [System.IO.Path]::DirectorySeparatorChar,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Refusing unexpected self-extract target: $fullTarget"
    }
    if (Test-Path -LiteralPath $fullTarget) {
        Remove-Item -LiteralPath $fullTarget -Force
    }
}

Push-Location -LiteralPath $packageDirectory
try {
    & $sevenZip a -t7z -mx=1 -mmt=on $payload '.\*'
    if ($LASTEXITCODE -ne 0) { throw "7-Zip payload creation failed: $LASTEXITCODE" }
} finally {
    Pop-Location
}

$output = [System.IO.File]::Open(
    $selfExtract,
    [System.IO.FileMode]::CreateNew,
    [System.IO.FileAccess]::Write,
    [System.IO.FileShare]::None
)
try {
    foreach ($inputPath in @($sfxModule, $payload)) {
        $input = [System.IO.File]::OpenRead($inputPath)
        try { $input.CopyTo($output, 16MB) } finally { $input.Dispose() }
    }
} finally {
    $output.Dispose()
}

& $sevenZip t $selfExtract
if ($LASTEXITCODE -ne 0) { throw "Self-extract integrity test failed: $LASTEXITCODE" }
[System.IO.File]::Delete($payload)

$certificatePath = $env:T8_WINDOWS_CERTIFICATE_PATH
$certificatePassword = $env:T8_WINDOWS_CERTIFICATE_PASSWORD
if (($certificatePath -and -not $certificatePassword) -or
    ($certificatePassword -and -not $certificatePath)) {
    throw 'Code signing requires both T8_WINDOWS_CERTIFICATE_PATH and T8_WINDOWS_CERTIFICATE_PASSWORD.'
}
if ($certificatePath) {
    Push-Location -LiteralPath $desktopRoot
    try {
        node (Join-Path $desktopRoot 'scripts\sign-windows-executable.js') $selfExtract
        if ($LASTEXITCODE -ne 0) { throw 'Self-extract Authenticode signing failed.' }
    } finally {
        Pop-Location
    }
    & $sevenZip t $selfExtract
    if ($LASTEXITCODE -ne 0) { throw "Signed self-extract integrity test failed: $LASTEXITCODE" }
}
Write-Host "Self-extract package: $selfExtract"
