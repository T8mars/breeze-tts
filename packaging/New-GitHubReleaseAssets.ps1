[CmdletBinding()]
param(
    [string]$ArtifactRoot = (Join-Path $PSScriptRoot '..\desktop\out\make'),
    [ValidateRange(104857600, 2100000000)]
    [int64]$PartSizeBytes = 1900MB
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$package = Get-Content -LiteralPath (Join-Path $projectRoot 'desktop\package.json') -Raw | ConvertFrom-Json
$version = [string]$package.version
$packageBaseName = "T8star-Aix-Voice-Studio-v$version"
$artifactRootPath = (Resolve-Path -LiteralPath $ArtifactRoot).Path
$sourcePath = Join-Path $artifactRootPath "self-extract\win32\x64\$packageBaseName-SelfExtract.exe"
if (-not (Test-Path -LiteralPath $sourcePath -PathType Leaf)) {
    throw "Self-extracting package is missing: $sourcePath"
}

$githubRoot = [System.IO.Path]::GetFullPath((Join-Path $artifactRootPath 'github'))
$outputDirectory = [System.IO.Path]::GetFullPath((Join-Path $githubRoot "v$version"))
$artifactPrefix = $artifactRootPath.TrimEnd([System.IO.Path]::DirectorySeparatorChar) +
    [System.IO.Path]::DirectorySeparatorChar
$githubPrefix = $githubRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) +
    [System.IO.Path]::DirectorySeparatorChar
if (-not $githubRoot.StartsWith($artifactPrefix, [System.StringComparison]::OrdinalIgnoreCase) -or
    -not $outputDirectory.StartsWith($githubPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing GitHub release output outside the artifact directory: $outputDirectory"
}
if (Test-Path -LiteralPath $outputDirectory) {
    Remove-Item -LiteralPath $outputDirectory -Recurse -Force
}
New-Item -ItemType Directory -Path $outputDirectory | Out-Null

$source = Get-Item -LiteralPath $sourcePath
$sourceHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
$partPaths = @()
$input = [System.IO.File]::OpenRead($sourcePath)
try {
    $buffer = [byte[]]::new(16MB)
    $partIndex = 1
    while ($input.Position -lt $input.Length) {
        $partName = "$packageBaseName-SelfExtract.exe.part-$('{0:d3}' -f $partIndex)"
        $partPath = Join-Path $outputDirectory $partName
        $remaining = [Math]::Min($PartSizeBytes, $input.Length - $input.Position)
        $output = [System.IO.File]::Open($partPath, [System.IO.FileMode]::CreateNew)
        try {
            while ($remaining -gt 0) {
                $count = [int][Math]::Min($buffer.Length, $remaining)
                $read = $input.Read($buffer, 0, $count)
                if ($read -le 0) { throw "Unexpected end of file while splitting $sourcePath" }
                $output.Write($buffer, 0, $read)
                $remaining -= $read
            }
        } finally {
            $output.Dispose()
        }
        $partPaths += $partPath
        $partIndex += 1
    }
} finally {
    $input.Dispose()
}

if ($partPaths.Count -lt 2) {
    throw 'The package did not require splitting; publish the original artifact instead.'
}
foreach ($partPath in $partPaths) {
    if ((Get-Item -LiteralPath $partPath).Length -ge 2GB) {
        throw "GitHub release part is too large: $partPath"
    }
}

$partNames = @($partPaths | ForEach-Object { [System.IO.Path]::GetFileName($_) })
$copyExpression = ($partNames | ForEach-Object { '"%~dp0' + $_ + '"' }) -join '+'
$joinedName = "$packageBaseName-SelfExtract.exe"
$joinScriptName = "Join-and-Run-$packageBaseName.cmd"
$joinScriptPath = Join-Path $outputDirectory $joinScriptName
$joinScript = @"
@echo off
setlocal
cd /d "%~dp0"
echo Rebuilding $joinedName ...
copy /b $copyExpression "$joinedName" >nul
if errorlevel 1 goto :failed
echo Verifying SHA-256 ...
certutil -hashfile "$joinedName" SHA256 | findstr /i "$sourceHash" >nul
if errorlevel 1 goto :hashfailed
echo Integrity verified. Starting the self-extracting package.
start "" "$joinedName"
exit /b 0
:hashfailed
echo ERROR: SHA-256 verification failed. Delete the rebuilt EXE and download every part again.
del /q "$joinedName" >nul 2>&1
pause
exit /b 2
:failed
echo ERROR: Could not rebuild the package. Keep this CMD file beside every .part-### file.
pause
exit /b 1
"@
[System.IO.File]::WriteAllText($joinScriptPath, $joinScript, [System.Text.Encoding]::ASCII)

$checksumLines = @($partPaths | ForEach-Object {
    $hash = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()
    "$hash  $([System.IO.Path]::GetFileName($_))"
})
$checksumLines += "$sourceHash  $joinedName"
$checksumLines += "$((Get-FileHash -LiteralPath $joinScriptPath -Algorithm SHA256).Hash.ToLowerInvariant())  $joinScriptName"
$checksumPath = Join-Path $outputDirectory 'SHA256SUMS-GITHUB.txt'
[System.IO.File]::WriteAllLines($checksumPath, $checksumLines, [System.Text.UTF8Encoding]::new($false))

Write-Host "GitHub release assets: $outputDirectory"
Write-Host "Original SHA-256: $sourceHash"
Get-ChildItem -LiteralPath $outputDirectory -File | Select-Object Name, Length
