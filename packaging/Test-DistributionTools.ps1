[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$desktopPackage = Get-Content -LiteralPath (Join-Path $projectRoot 'desktop\package.json') -Raw |
    ConvertFrom-Json
$releaseVersion = [string]$desktopPackage.version
$packageRoot = [System.IO.Path]::GetFullPath((Join-Path $projectRoot '.package'))
$testRoot = Join-Path $packageRoot "distribution-test-$PID"
$resolvedTestRoot = [System.IO.Path]::GetFullPath($testRoot)
if (-not $resolvedTestRoot.StartsWith(
    $packageRoot + [System.IO.Path]::DirectorySeparatorChar,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "Refusing to use unsafe distribution test path: $resolvedTestRoot"
}

try {
    New-Item -ItemType Directory -Force -Path (Join-Path $resolvedTestRoot 'zip\win32\x64') | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $resolvedTestRoot 'self-extract\win32\x64') | Out-Null
    New-Item -ItemType Directory -Force -Path (Join-Path $resolvedTestRoot 'squirrel.windows\x64') | Out-Null
    [System.IO.File]::WriteAllText(
        (Join-Path $resolvedTestRoot "zip\win32\x64\portable-$releaseVersion.zip"),
        'portable-test',
        [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText(
        (Join-Path $resolvedTestRoot "self-extract\win32\x64\T8-v$releaseVersion-SelfExtract.exe"),
        'unsigned-self-extract-test',
        [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText(
        (Join-Path $resolvedTestRoot "squirrel.windows\x64\T8-v$releaseVersion-Setup.exe"),
        'unsigned-installer-test',
        [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText(
        (Join-Path $resolvedTestRoot "squirrel.windows\x64\T8-$releaseVersion-full.nupkg"),
        'package-test',
        [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText(
        (Join-Path $resolvedTestRoot 'zip\win32\x64\portable-0.0.1.zip'),
        'stale-portable-test',
        [System.Text.UTF8Encoding]::new($false)
    )
    [System.IO.File]::WriteAllText(
        (Join-Path $resolvedTestRoot 'squirrel.windows\x64\RELEASES'),
        'release-test',
        [System.Text.UTF8Encoding]::new($false)
    )

    & (Join-Path $PSScriptRoot 'New-ReleaseManifest.ps1') `
        -ArtifactRoot $resolvedTestRoot `
        -RequireWindowsPackage `
        -RequireInstaller
    $manifestPath = Join-Path $resolvedTestRoot 'release-manifest.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if (@($manifest.artifacts | Where-Object { $_.path -like '*0.0.1*' }).Count -ne 0) {
        throw 'A stale-version artifact was included in the release manifest.'
    }
    if (@($manifest.artifacts | Where-Object { $_.kind -eq 'windows-self-extract' }).Count -ne 1) {
        throw 'The self-extracting package was not classified correctly.'
    }
    if (@($manifest.artifacts | Where-Object { $_.kind -eq 'windows-installer' }).Count -ne 1) {
        throw 'The Squirrel Setup executable was not classified as an installer.'
    }
    $manifestHash = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
    & (Join-Path $PSScriptRoot 'Test-ReleaseManifest.ps1') `
        -ManifestPath $manifestPath `
        -ArtifactRoot $resolvedTestRoot `
        -ExpectedManifestSha256 $manifestHash `
        -RequireWindowsPackage `
        -RequireInstaller

    $unsignedRejected = $false
    try {
        & (Join-Path $PSScriptRoot 'Test-ReleaseManifest.ps1') `
            -ManifestPath $manifestPath `
            -ArtifactRoot $resolvedTestRoot `
            -ExpectedManifestSha256 $manifestHash `
            -RequireWindowsPackage `
            -RequireInstaller `
            -RequireSignedWindows
    } catch {
        $unsignedRejected = $_.Exception.Message -like '*signature is not valid*'
    }
    if (-not $unsignedRejected) { throw 'Unsigned installer was not rejected by the strict verifier.' }

    $portablePath = Join-Path $resolvedTestRoot "zip\win32\x64\portable-$releaseVersion.zip"
    [System.IO.File]::AppendAllText($portablePath, '-tampered')
    $tamperDetected = $false
    try {
        & (Join-Path $PSScriptRoot 'Test-ReleaseManifest.ps1') `
            -ManifestPath $manifestPath `
            -ArtifactRoot $resolvedTestRoot `
            -RequireWindowsPackage `
            -RequireInstaller
    } catch {
        $tamperDetected = $_.Exception.Message -like '*mismatch*'
    }
    if (-not $tamperDetected) { throw 'Tampered artifact was not rejected by the verifier.' }

    Push-Location (Join-Path $projectRoot 'desktop')
    try {
        npm run verify:distribution
        if ($LASTEXITCODE -ne 0) { throw 'Forge distribution configuration check failed.' }
    } finally {
        Pop-Location
    }
    Write-Host 'Distribution tooling self-test passed, including package classification and tamper detection.'
} finally {
    if (Test-Path -LiteralPath $resolvedTestRoot) {
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
    }
}
