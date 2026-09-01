# Windows distribution and offline verification

The Windows release contains a portable ZIP and a 7-Zip self-extracting EXE. The
bundled CUDA/PyTorch runtime is larger than Squirrel's reliable embedded-Setup
limit, so the production release uses a manual update manifest instead of emitting
a corrupt Squirrel installer. Automatic checks remain disabled unless the packaged
app is started with `T8_BREEZE_UPDATE_URL` set to a credential-free HTTPS URL; no
repository or update server is hard-coded.

## Build targets

Run from the repository root in PowerShell:

```powershell
# Existing portable ZIP only
.\packaging\build_portable.ps1

# Portable ZIP + self-extracting EXE + checksum manifest
.\packaging\build_release.ps1
```

Use Node.js 22.12 or newer; CI builds with Node 22 and `npm ci` from the committed
lockfile.

The full build is intentionally large and may take a long time because the bundled
Python/CUDA runtime is copied and compressed. The self-extract target requires a
local 7-Zip 25.x installation. The model weights remain excluded.
Cached Electron archives are accepted only when their SHA-256 matches
`electron-checksums.json`; Forge downloads are also verified by Electron's download
tool. Update that pinned checksum from the official Electron release whenever the
Electron version changes.

## GitHub Release assets

GitHub requires every individual Release asset to remain below 2 GiB. The bundled
Windows runtime is larger even after compression, so split the verified
self-extracting package after `build_release.ps1` completes:

```powershell
.\packaging\New-GitHubReleaseAssets.ps1
```

Publish every `.part-###` file, the generated `Join-and-Run-*.cmd`, and
`SHA256SUMS-GITHUB.txt` together. Users place all files in one directory and run
the CMD file; it rebuilds the original self-extracting EXE, verifies its SHA-256,
and only then starts it. The generated EXE is unsigned unless the original release
was built through the Authenticode workflow described below.

## Build-tool audit policy

The desktop application has no production npm dependencies, and CI requires the
complete `npm audit` to remain clean. Safe overrides keep `tar` and `tmp` on patched
releases and replace Electron Packager's unmaintained `extract-zip` dependency with
Electron's hardened, drop-in `@electron-internal/extract-zip`. The pinned Electron
archive checksum limits build input to trusted, hash-verified archives.

## Optional Authenticode signing

No development or test certificate is generated. With no variables set, the build
emits unsigned files. Sign the final self-extracting EXE with the publisher's normal
Authenticode pipeline before strict verification. Certificate variables apply to
the packaged application and automatically sign the final self-extractor after it
is assembled:

```powershell
$env:T8_WINDOWS_CERTIFICATE_PATH = 'D:\secure\publisher.pfx'
$env:T8_WINDOWS_CERTIFICATE_PASSWORD = '<secret-from-secure-store>'
$env:T8_WINDOWS_TIMESTAMP_SERVER = 'http://timestamp.digicert.com' # optional
.\packaging\build_release.ps1 -RequireSignedWindows
```

Never commit the PFX or password. `-RequireSignedWindows` makes release generation
fail unless every distributable Windows executable has a valid Authenticode signature.

## Optional update checks

Publish the new manifest and artifacts to one HTTPS directory, then set the feed
for the packaged application before launch:

```powershell
$env:T8_BREEZE_UPDATE_URL = 'https://downloads.example.com/breeze/windows/'
```

The application accepts only HTTPS URLs without embedded credentials. Without a
valid feed it makes no update request and keeps the offline manifest verifier
available. Version 0.2.3 uses manual download/replace updates; production EXEs
should be Authenticode signed by the same trusted publisher.

## Offline integrity check

Publish `release-manifest.json`, `release-manifest.json.sha256`, and
`SHA256SUMS.txt` beside the artifacts. A checksum file downloaded from the same
untrusted location is not a trust anchor. Obtain the manifest SHA-256 through an
independent trusted channel, then run:

```powershell
.\packaging\Test-ReleaseManifest.ps1 `
  -ManifestPath .\download\release-manifest.json `
  -ArtifactRoot .\download `
  -ExpectedManifestSha256 '<trusted-64-character-sha256>' `
  -RequireWindowsPackage
```

Use `-RequireSignedWindows` when the release policy requires Authenticode. The
verifier rejects absolute/traversal paths, duplicate entries, missing files, size
or SHA-256 mismatches, unexpected checksum entries, and invalid signatures.

## Fast release-tool validation

This does not build the 3+ GB application. It validates the portable Forge target,
both signing branches, the pinned Electron archive checksum, package classification,
manifest generation, offline verification, and tamper rejection:

```powershell
.\packaging\Test-DistributionTools.ps1
```
