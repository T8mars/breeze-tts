[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ManifestPath,
    [string]$ArtifactRoot,
    [string]$ExpectedManifestSha256,
    [switch]$RequireWindowsPackage,
    [switch]$RequireInstaller,
    [switch]$RequireSignedWindows
)

$ErrorActionPreference = 'Stop'
$resolvedManifest = (Resolve-Path -LiteralPath $ManifestPath).Path
if (-not $ArtifactRoot) {
    $ArtifactRoot = Split-Path -Parent $resolvedManifest
}
$root = (Resolve-Path -LiteralPath $ArtifactRoot).Path
$rootPrefix = $root.TrimEnd([System.IO.Path]::DirectorySeparatorChar) +
    [System.IO.Path]::DirectorySeparatorChar
$manifestFullPath = [System.IO.Path]::GetFullPath($resolvedManifest)
if (-not $manifestFullPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw 'ManifestPath must be located below ArtifactRoot.'
}

$actualManifestHash = (Get-FileHash -LiteralPath $manifestFullPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($ExpectedManifestSha256) {
    $expected = $ExpectedManifestSha256.Trim().ToLowerInvariant()
    if ($expected -notmatch '^[0-9a-f]{64}$') {
        throw 'ExpectedManifestSha256 must contain exactly 64 hexadecimal characters.'
    }
    if ($actualManifestHash -ne $expected) {
        throw "Manifest trust-anchor mismatch: expected $expected, got $actualManifestHash"
    }
}

$manifest = Get-Content -LiteralPath $manifestFullPath -Raw | ConvertFrom-Json
if ($manifest.schemaVersion -ne 1) { throw "Unsupported schemaVersion: $($manifest.schemaVersion)" }
if ([string]::IsNullOrWhiteSpace([string]$manifest.product)) { throw 'Manifest product is missing.' }
if ([string]::IsNullOrWhiteSpace([string]$manifest.version)) { throw 'Manifest version is missing.' }
if (-not $manifest.artifacts -or @($manifest.artifacts).Count -eq 0) { throw 'Manifest has no artifacts.' }

$seen = @{}
$verified = @{}
$installerCount = 0
$windowsPackageCount = 0
foreach ($entry in @($manifest.artifacts)) {
    $relative = [string]$entry.path
    if ([string]::IsNullOrWhiteSpace($relative) -or
        [System.IO.Path]::IsPathRooted($relative) -or
        $relative -match '(^|[\\/])\.\.([\\/]|$)') {
        throw "Unsafe artifact path in manifest: $relative"
    }
    $key = $relative.Replace('\', '/').ToLowerInvariant()
    if ($seen.ContainsKey($key)) { throw "Duplicate artifact path: $relative" }
    $seen[$key] = $true
    $artifactPath = [System.IO.Path]::GetFullPath((Join-Path $root $relative))
    if (-not $artifactPath.StartsWith($rootPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Artifact escaped ArtifactRoot: $relative"
    }
    if (-not (Test-Path -LiteralPath $artifactPath -PathType Leaf)) {
        throw "Artifact is missing: $relative"
    }
    $file = Get-Item -LiteralPath $artifactPath
    if ([int64]$entry.size -ne [int64]$file.Length) {
        throw "Artifact size mismatch: $relative"
    }
    $expectedHash = ([string]$entry.sha256).ToLowerInvariant()
    if ($expectedHash -notmatch '^[0-9a-f]{64}$') { throw "Invalid SHA-256: $relative" }
    $actualHash = (Get-FileHash -LiteralPath $artifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $expectedHash) { throw "Artifact hash mismatch: $relative" }
    $verified[$key] = $actualHash
    if ([string]$entry.kind -eq 'windows-installer') {
        $installerCount += 1
    }
    if ([string]$entry.kind -in @('windows-installer', 'windows-self-extract')) {
        $windowsPackageCount += 1
        if ($RequireSignedWindows) {
            $signature = Get-AuthenticodeSignature -LiteralPath $artifactPath
            if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {
                throw "Windows package signature is not valid ($($signature.Status)): $relative"
            }
        }
    }
}
if ($RequireWindowsPackage -and $windowsPackageCount -eq 0) {
    throw 'A distributable Windows package is required.'
}
if ($RequireInstaller -and $installerCount -eq 0) { throw 'A Windows installer is required.' }

$checksumPath = Join-Path (Split-Path -Parent $manifestFullPath) 'SHA256SUMS.txt'
if (-not (Test-Path -LiteralPath $checksumPath -PathType Leaf)) { throw 'SHA256SUMS.txt is missing.' }
$checksumEntries = @{}
foreach ($line in Get-Content -LiteralPath $checksumPath) {
    if ([string]::IsNullOrWhiteSpace($line)) { continue }
    if ($line -notmatch '^([0-9a-fA-F]{64})  (.+)$') { throw "Malformed checksum line: $line" }
    $pathKey = $Matches[2].Replace('\', '/').ToLowerInvariant()
    if ($checksumEntries.ContainsKey($pathKey)) { throw "Duplicate checksum path: $($Matches[2])" }
    $checksumEntries[$pathKey] = $Matches[1].ToLowerInvariant()
}
foreach ($entry in @($manifest.artifacts)) {
    $key = ([string]$entry.path).Replace('\', '/').ToLowerInvariant()
    if (-not $checksumEntries.ContainsKey($key) -or $checksumEntries[$key] -ne $verified[$key]) {
        throw "SHA256SUMS.txt does not match artifact: $($entry.path)"
    }
}
$manifestRelative = $manifestFullPath.Substring($rootPrefix.Length).Replace('\', '/').ToLowerInvariant()
if (-not $checksumEntries.ContainsKey($manifestRelative) -or
    $checksumEntries[$manifestRelative] -ne $actualManifestHash) {
    throw 'SHA256SUMS.txt does not match release-manifest.json.'
}
if ($checksumEntries.Count -ne (@($manifest.artifacts).Count + 1)) {
    throw 'SHA256SUMS.txt contains unexpected or missing entries.'
}

$sidecarPath = "$manifestFullPath.sha256"
if (Test-Path -LiteralPath $sidecarPath -PathType Leaf) {
    $sidecar = (Get-Content -LiteralPath $sidecarPath -Raw).Trim()
    if ($sidecar -notmatch '^([0-9a-fA-F]{64})  release-manifest\.json$' -or
        $Matches[1].ToLowerInvariant() -ne $actualManifestHash) {
        throw 'release-manifest.json.sha256 does not match the manifest.'
    }
}

Write-Host "Verified $(@($manifest.artifacts).Count) artifact(s) for $($manifest.product) $($manifest.version)."
Write-Host "Manifest SHA-256: $actualManifestHash"
