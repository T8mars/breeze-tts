[CmdletBinding()]
param(
    [switch]$SkipRuntime,
    [switch]$SkipNpmInstall,
    [ValidatePattern('^[a-z0-9][a-z0-9._-]*$')]
    [string]$Channel = 'stable',
    [switch]$RequireSignedWindows
)

$ErrorActionPreference = 'Stop'
$arguments = @{}
if ($SkipRuntime) { $arguments.SkipRuntime = $true }
if ($SkipNpmInstall) { $arguments.SkipNpmInstall = $true }

& (Join-Path $PSScriptRoot 'build_portable.ps1') @arguments
& (Join-Path $PSScriptRoot 'build_self_extract.ps1')

$artifactRoot = Join-Path $PSScriptRoot '..\desktop\out\make'
$manifestArguments = @{
    ArtifactRoot = $artifactRoot
    Channel = $Channel
    RequireWindowsPackage = $true
}
if ($RequireSignedWindows) { $manifestArguments.RequireSignedWindows = $true }
& (Join-Path $PSScriptRoot 'New-ReleaseManifest.ps1') @manifestArguments

$verifyArguments = @{
    ManifestPath = (Join-Path $artifactRoot 'release-manifest.json')
    ArtifactRoot = $artifactRoot
    RequireWindowsPackage = $true
}
if ($RequireSignedWindows) { $verifyArguments.RequireSignedWindows = $true }
& (Join-Path $PSScriptRoot 'Test-ReleaseManifest.ps1') @verifyArguments

Write-Host "Release is ready below $artifactRoot"
