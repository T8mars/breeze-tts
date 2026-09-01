[CmdletBinding()]
param(
    [string]$ArtifactRoot = (Join-Path $PSScriptRoot '..\desktop\out\make'),
    [string]$OutputDirectory = $ArtifactRoot,
    [ValidatePattern('^[a-z0-9][a-z0-9._-]*$')]
    [string]$Channel = 'stable',
    [switch]$RequireWindowsPackage,
    [switch]$RequireInstaller,
    [switch]$RequireSignedWindows
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$packageJsonPath = Join-Path $projectRoot 'desktop\package.json'
$package = Get-Content -LiteralPath $packageJsonPath -Raw | ConvertFrom-Json
$releaseVersion = [string]$package.version
$artifactRootPath = (Resolve-Path -LiteralPath $ArtifactRoot).Path
$outputPath = [System.IO.Path]::GetFullPath($OutputDirectory)
$artifactPrefix = $artifactRootPath.TrimEnd([System.IO.Path]::DirectorySeparatorChar) +
    [System.IO.Path]::DirectorySeparatorChar

if (-not $outputPath.StartsWith($artifactPrefix, [System.StringComparison]::OrdinalIgnoreCase) -and
    $outputPath -ne $artifactRootPath) {
    throw "OutputDirectory must be ArtifactRoot or a directory below it: $outputPath"
}
New-Item -ItemType Directory -Force -Path $outputPath | Out-Null

$excludedNames = @('release-manifest.json', 'release-manifest.json.sha256', 'SHA256SUMS.txt')
$artifactFiles = @(Get-ChildItem -LiteralPath $artifactRootPath -Recurse -File | Where-Object {
    if ($_.Name -in $excludedNames) { return $false }
    if ($_.Name -eq 'RELEASES') { return $true }
    $isReleaseArtifact = $_.Extension.ToLowerInvariant() -in @('.zip', '.exe', '.nupkg')
    $hasCurrentVersion = $_.Name.IndexOf(
        $releaseVersion,
        [System.StringComparison]::OrdinalIgnoreCase
    ) -ge 0
    return $isReleaseArtifact -and $hasCurrentVersion
} | Sort-Object FullName)
if ($artifactFiles.Count -eq 0) {
    throw "No release artifacts were found below $artifactRootPath"
}

function Get-ArtifactKind([System.IO.FileInfo]$File) {
    if ($File.Name -eq 'RELEASES') { return 'squirrel-releases' }
    switch ($File.Extension.ToLowerInvariant()) {
        '.zip' { return 'portable-zip' }
        '.exe' {
            if ($File.Name -match '(?i)self[-_ ]?extract') { return 'windows-self-extract' }
            if ($File.Name -match '(?i)(^|[-_ ])setup\.exe$') { return 'windows-installer' }
            return 'windows-executable'
        }
        '.nupkg' { return 'squirrel-package' }
        default { return 'artifact' }
    }
}

$entries = @()
$signedExeCount = 0
$exeCount = 0
foreach ($file in $artifactFiles) {
    $fullPath = [System.IO.Path]::GetFullPath($file.FullName)
    if (-not $fullPath.StartsWith($artifactPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Artifact escaped ArtifactRoot: $fullPath"
    }
    $relativePath = $fullPath.Substring($artifactPrefix.Length).Replace('\', '/')
    if ([string]::IsNullOrWhiteSpace($relativePath) -or $relativePath.Contains('../')) {
        throw "Unsafe artifact path: $relativePath"
    }
    $entry = [ordered]@{
        path = $relativePath
        kind = Get-ArtifactKind $file
        size = [int64]$file.Length
        sha256 = (Get-FileHash -LiteralPath $fullPath -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    if ($file.Extension -ieq '.exe') {
        $exeCount += 1
        $signature = Get-AuthenticodeSignature -LiteralPath $fullPath
        $entry.authenticode = [ordered]@{
            status = [string]$signature.Status
            signerThumbprint = if ($signature.SignerCertificate) {
                $signature.SignerCertificate.Thumbprint.ToLowerInvariant()
            } else {
                $null
            }
        }
        if ($signature.Status -eq [System.Management.Automation.SignatureStatus]::Valid) {
            $signedExeCount += 1
        }
    }
    $entries += [pscustomobject]$entry
}

if ($RequireWindowsPackage -and -not ($entries | Where-Object {
    $_.kind -in @('windows-installer', 'windows-self-extract')
})) {
    throw 'A distributable Windows package is required, but no installer or self-extracting package was found.'
}
if ($RequireInstaller -and -not ($entries | Where-Object { $_.kind -eq 'windows-installer' })) {
    throw 'A Windows installer is required, but no .exe installer artifact was found.'
}
if ($RequireSignedWindows -and $signedExeCount -ne $exeCount) {
    throw "All distributable Windows executables must have a valid Authenticode signature ($signedExeCount/$exeCount valid)."
}

$releasesEntry = $entries | Where-Object { $_.kind -eq 'squirrel-releases' } | Select-Object -First 1
$fullPackageEntry = $entries | Where-Object { $_.kind -eq 'squirrel-package' -and $_.path -match '-full\.nupkg$' } | Select-Object -First 1
$installerEntry = $entries | Where-Object { $_.kind -eq 'windows-installer' } | Select-Object -First 1
$selfExtractEntry = $entries | Where-Object { $_.kind -eq 'windows-self-extract' } | Select-Object -First 1
if ($env:SOURCE_DATE_EPOCH -and $env:SOURCE_DATE_EPOCH -match '^\d+$') {
    $generatedAt = [DateTimeOffset]::FromUnixTimeSeconds([int64]$env:SOURCE_DATE_EPOCH).UtcDateTime
} else {
    $generatedAt = [DateTime]::UtcNow
}
$signingStatus = if ($exeCount -eq 0) {
    'not-applicable'
} elseif ($signedExeCount -eq $exeCount) {
    'signed'
} elseif ($signedExeCount -eq 0) {
    'unsigned'
} else {
    'mixed'
}

$manifest = [ordered]@{
    schemaVersion = 1
    product = [string]$package.productName
    version = [string]$package.version
    channel = $Channel
    generatedAt = $generatedAt.ToString('yyyy-MM-ddTHH:mm:ssZ')
    platform = 'win32'
    arch = 'x64'
    signing = [ordered]@{
        policy = 'optional-authenticode'
        status = $signingStatus
    }
    update = [ordered]@{
        strategy = if ($releasesEntry -and $fullPackageEntry) { 'squirrel-windows' } else { 'manual' }
        releasesPath = if ($releasesEntry) { $releasesEntry.path } else { $null }
        fullPackagePath = if ($fullPackageEntry) { $fullPackageEntry.path } else { $null }
        installerPath = if ($installerEntry) { $installerEntry.path } else { $null }
        selfExtractPath = if ($selfExtractEntry) { $selfExtractEntry.path } else { $null }
    }
    artifacts = $entries
}

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
$manifestPath = Join-Path $outputPath 'release-manifest.json'
$manifestJson = $manifest | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText($manifestPath, "$manifestJson`n", $utf8NoBom)
$manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()

$checksumLines = @($entries | Sort-Object path | ForEach-Object { "$($_.sha256)  $($_.path)" })
$manifestRelative = [System.IO.Path]::GetFullPath($manifestPath).Substring($artifactPrefix.Length).Replace('\', '/')
$checksumLines += "$manifestHash  $manifestRelative"
$checksumPath = Join-Path $outputPath 'SHA256SUMS.txt'
[System.IO.File]::WriteAllLines($checksumPath, $checksumLines, $utf8NoBom)
$manifestSidecar = Join-Path $outputPath 'release-manifest.json.sha256'
[System.IO.File]::WriteAllText($manifestSidecar, "$manifestHash  release-manifest.json`n", $utf8NoBom)

Write-Host "Release manifest: $manifestPath"
Write-Host "Manifest SHA-256: $manifestHash"
Write-Host "Signing status: $signingStatus"
