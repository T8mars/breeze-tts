[CmdletBinding()]
param(
    [ValidatePattern('^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$')]
    [string]$Version
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.IO.Compression.FileSystem
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$source = Join-Path $projectRoot 'comfyui-breeze-tts-T8'
$pyprojectPath = Join-Path $source 'pyproject.toml'
$pyprojectText = Get-Content -LiteralPath $pyprojectPath -Raw
if ($pyprojectText -notmatch '(?m)^version\s*=\s*"([^"]+)"\s*$') {
    throw "Unable to read the ComfyUI package version from $pyprojectPath"
}
$declaredVersion = $Matches[1]
if (-not $Version) { $Version = $declaredVersion }
if ($Version -ne $declaredVersion) {
    throw "Requested version $Version does not match pyproject.toml version $declaredVersion."
}
$entrypointText = Get-Content -LiteralPath (Join-Path $source '__init__.py') -Raw
if ($entrypointText -notmatch ('(?m)^__version__\s*=\s*"' + [regex]::Escape($Version) + '"\s*$')) {
    throw "__init__.py does not declare version $Version."
}
$dist = Join-Path $projectRoot 'dist'
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$zipPath = Join-Path $dist "comfyui-breeze-tts-T8-v$Version.zip"
if (Test-Path -LiteralPath $zipPath) {
    throw "Artifact already exists: $zipPath. Bump -Version or move the existing release first."
}

$stream = [System.IO.File]::Open($zipPath, [System.IO.FileMode]::CreateNew)
try {
    $archive = [System.IO.Compression.ZipArchive]::new(
        $stream,
        [System.IO.Compression.ZipArchiveMode]::Create,
        $false
    )
    try {
        Get-ChildItem -LiteralPath $source -Recurse -File | Where-Object {
            $_.FullName -notmatch '[\\/]__pycache__[\\/]' -and
            $_.Extension -notin @('.pyc', '.pyo') -and
            $_.FullName -notmatch '[\\/]\.ruff_cache[\\/]' -and
            $_.FullName -notmatch '[\\/]\.pytest_cache[\\/]'
        } | ForEach-Object {
            $relative = $_.FullName.Substring($source.Length + 1).Replace('\', '/')
            $entryName = "comfyui-breeze-tts-T8/$relative"
            [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $archive,
                $_.FullName,
                $entryName,
                [System.IO.Compression.CompressionLevel]::Optimal
            ) | Out-Null
        }
    } finally {
        $archive.Dispose()
    }
} finally {
    $stream.Dispose()
}

$hash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash.ToLowerInvariant()
$checksum = "$hash  $([System.IO.Path]::GetFileName($zipPath))"
$checksumPath = Join-Path $dist 'comfyui-breeze-tts-T8-SHA256.txt'
[System.IO.File]::WriteAllText($checksumPath, "$checksum`n", [System.Text.UTF8Encoding]::new($false))
Write-Host $checksum
