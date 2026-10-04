[CmdletBinding()]
param(
    [string]$SourceRoot = 'E:\Confucius4-R2T2',
    [string]$NativeBuildDir,
    [string]$WorkerPython,
    [string]$RuntimeRoot = (Join-Path $PSScriptRoot '..\.runtime\confucius'),
    [string]$ModelRoot = (Join-Path $PSScriptRoot '..\.runtime\confucius-models\Confucius4-R2T2-GGUF'),
    [string]$CudaToolkit = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8',
    [string]$VisualStudioRoot = 'C:\Program Files\Microsoft Visual Studio\2022\Community',
    [switch]$CopyModels
)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$sourcePath = (Resolve-Path -LiteralPath $SourceRoot).Path
$runtimePath = [IO.Path]::GetFullPath($RuntimeRoot)
$modelPath = [IO.Path]::GetFullPath($ModelRoot)
$manifest = Get-Content -LiteralPath (Join-Path $projectRoot 'manifests\confucius-runtime.json') -Raw | ConvertFrom-Json
$sourceCommit = (& git -C $sourcePath rev-parse --verify "$($manifest.source.commit)^{commit}").Trim()
if ($LASTEXITCODE -ne 0 -or $sourceCommit -ne $manifest.source.commit) {
    throw "Confucius source must contain pinned commit $($manifest.source.commit), found $sourceCommit"
}
if (-not $WorkerPython) { $WorkerPython = Join-Path $sourcePath '.runtime\worker\Scripts\python.exe' }
$workerPath = (Resolve-Path -LiteralPath $WorkerPython).Path
$workerInfoText = & $workerPath -I -B -c 'import json,sys,struct,platform; print(json.dumps({"version":list(sys.version_info[:2]),"full_version":platform.python_version(),"bits":struct.calcsize("P")*8,"base":sys.base_prefix,"prefix":sys.prefix}))'
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect the source worker Python.' }
$workerInfo = $workerInfoText | ConvertFrom-Json
if ($workerInfo.bits -ne 64 -or ($workerInfo.version -join '.') -ne '3.12') {
    throw 'Confucius requires a 64-bit CPython 3.12 worker.'
}
if ($workerInfo.full_version -ne $manifest.python.version) {
    throw "Worker Python must match the license-audited version $($manifest.python.version)."
}
$basePath = (Resolve-Path -LiteralPath $workerInfo.base).Path
$workerPackages = Join-Path $workerInfo.prefix 'Lib\site-packages'
if (-not (Test-Path -LiteralPath $workerPackages -PathType Container)) {
    throw "Source worker site-packages are missing: $workerPackages"
}
if ($runtimePath.Equals($sourcePath, [StringComparison]::OrdinalIgnoreCase) -or
    $runtimePath.StartsWith($sourcePath + '\', [StringComparison]::OrdinalIgnoreCase) -or
    $runtimePath.Equals($basePath, [StringComparison]::OrdinalIgnoreCase) -or
    $runtimePath.StartsWith($basePath + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'The output runtime must be separate from the source project and base Python.'
}
& $workerPath -I -B (Join-Path $PSScriptRoot 'verify_confucius_runtime.py') --project-root $projectRoot --source-root $sourcePath --source-revision $sourceCommit --source-only
if ($LASTEXITCODE -ne 0) { throw 'Vendored Confucius source checksum verification failed.' }

if (-not $NativeBuildDir) { $NativeBuildDir = Join-Path $sourcePath '.runtime\build-native-cu128' }
$nativeSource = (Resolve-Path -LiteralPath $NativeBuildDir).Path
$nativeCache = Join-Path $nativeSource 'CMakeCache.txt'
if (-not (Test-Path -LiteralPath $nativeCache -PathType Leaf)) { throw 'Native CMakeCache.txt is missing; finish the native build first.' }
$cacheText = Get-Content -LiteralPath $nativeCache -Raw
$architectureMatch = [regex]::Match($cacheText, '(?m)^CMAKE_CUDA_ARCHITECTURES:[^=]+=(.+)$')
$builtArchitectures = $architectureMatch.Groups[1].Value.Trim().Split(';')
$requiredArchitectures = @($manifest.native.cuda_architectures)
if ((Compare-Object $builtArchitectures $requiredArchitectures)) {
    throw "Native CUDA architectures must be $($requiredArchitectures -join ';'); got $($builtArchitectures -join ';')."
}
$llamaMatch = [regex]::Match($cacheText, '(?m)^LLAMA_CPP_DIR:[^=]+=(.+)$')
if (-not $llamaMatch.Success) { throw 'Native build is missing its pinned llama.cpp source path.' }
$llamaSource = $llamaMatch.Groups[1].Value.Trim()
$llamaCommit = (& git -C $llamaSource rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $llamaCommit -ne $manifest.native.llama_cpp_commit) {
    throw "Native build must use llama.cpp commit $($manifest.native.llama_cpp_commit)."
}
$llamaChanges = & git -C $llamaSource status --porcelain --untracked-files=normal
if ($LASTEXITCODE -ne 0 -or $llamaChanges) {
    throw 'Native llama.cpp checkout must be clean at its pinned commit.'
}
$extensions = @(Get-ChildItem -LiteralPath (Join-Path $nativeSource 'python') -Recurse -File -Filter 'qwen3asr_native*.pyd')
if ($extensions.Count -ne 1 -or $extensions[0].Name -notmatch '\.cp312-win_amd64\.pyd$') {
    throw 'Expected exactly one completed CPython 3.12 Windows x64 native extension.'
}
$cudaPath = (Resolve-Path -LiteralPath $CudaToolkit).Path
# Inspect the completed CUDA DLL, not only CMake's requested targets. Architecture
# suffixes such as sm_90a/sm_120a still belong to their numeric device generation.
$cudaInspector = Join-Path $cudaPath 'bin\cuobjdump.exe'
$cudaDllSource = Join-Path $nativeSource 'bin\Release\ggml-cuda.dll'
if (-not (Test-Path -LiteralPath $cudaInspector -PathType Leaf) -or
    -not (Test-Path -LiteralPath $cudaDllSource -PathType Leaf)) {
    throw 'CUDA inspector or completed native CUDA DLL is missing.'
}
$fatbinOutput = & $cudaInspector --list-elf $cudaDllSource
if ($LASTEXITCODE -ne 0) { throw 'Unable to inspect the native CUDA fatbin.' }
$binaryArchitectures = @([regex]::Matches(($fatbinOutput -join [Environment]::NewLine), 'sm_(\d+)[af]?') |
    ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique)
foreach ($architecture in $requiredArchitectures) {
    if ($architecture -notin $binaryArchitectures) {
        throw "Native CUDA DLL is missing SM $architecture; found $($binaryArchitectures -join ';')."
    }
}
Write-Host "Native CUDA binary architecture coverage: $($binaryArchitectures -join ';')"
$redistBase = Join-Path $VisualStudioRoot 'VC\Redist\MSVC'
$vcRuntime = Get-ChildItem -LiteralPath $redistBase -Directory |
    Where-Object { $_.Name -match '^\d+\.\d+\.\d+$' } |
    Sort-Object { [version]$_.Name } -Descending |
    ForEach-Object { Join-Path $_.FullName 'x64\Microsoft.VC143.CRT' } |
    Where-Object { Test-Path -LiteralPath $_ -PathType Container } |
    Select-Object -First 1
if (-not $vcRuntime) { throw 'Official Visual Studio x64 release CRT redistributables were not found.' }

function Copy-Tree {
    param([string]$Source, [string]$Destination, [string[]]$ExcludedNames = @('__pycache__'))
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    foreach ($entry in Get-ChildItem -LiteralPath $Source -Force) {
        if ($entry.Name -in $ExcludedNames -or $entry.Extension -eq '.pyc') { continue }
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Refusing an external link in the portable runtime source: $($entry.FullName)"
        }
        $target = Join-Path $Destination $entry.Name
        if ($entry.PSIsContainer) { Copy-Tree -Source $entry.FullName -Destination $target }
        else { Copy-Item -LiteralPath $entry.FullName -Destination $target -Force }
    }
}

$pythonPath = Join-Path $runtimePath 'python'
if (Test-Path -LiteralPath (Join-Path $pythonPath 'pyvenv.cfg')) {
    throw 'Output contains a venv, not a portable Python. Use a fresh runtime directory.'
}
New-Item -ItemType Directory -Force -Path $pythonPath | Out-Null
foreach ($entry in Get-ChildItem -LiteralPath $basePath -File) {
    if ($entry.Name -in @('python.exe', 'pythonw.exe', 'LICENSE.txt') -or $entry.Extension -eq '.dll') {
        Copy-Item -LiteralPath $entry.FullName -Destination $pythonPath -Force
    }
}
Copy-Tree -Source (Join-Path $basePath 'DLLs') -Destination (Join-Path $pythonPath 'DLLs')
Copy-Tree -Source (Join-Path $basePath 'Lib') -Destination (Join-Path $pythonPath 'Lib') -ExcludedNames @('site-packages', '__pycache__')
# Preserve distributions and their bundled licenses, but omit local browser QA tools.
$packageExclusions = @('__pycache__', 'playwright', 'playwright-1.55.0.dist-info', 'greenlet', 'greenlet-3.5.6.dist-info', 'pyee', 'pyee-13.0.1.dist-info')
Copy-Tree -Source $workerPackages -Destination (Join-Path $pythonPath 'Lib\site-packages') -ExcludedNames $packageExclusions
foreach ($name in $manifest.native.vc_dlls) {
    Copy-Item -LiteralPath (Join-Path $vcRuntime $name) -Destination $pythonPath -Force
}

$nativePath = Join-Path $runtimePath 'native'
New-Item -ItemType Directory -Force -Path (Join-Path $nativePath 'python\Release'), (Join-Path $nativePath 'bin\Release') | Out-Null
Copy-Item -LiteralPath $extensions[0].FullName -Destination (Join-Path $nativePath 'python\Release') -Force
foreach ($name in $manifest.native.dlls) {
    Copy-Item -LiteralPath (Join-Path $nativeSource "bin\Release\$name") -Destination (Join-Path $nativePath 'bin\Release') -Force
}
$cudaBin = Join-Path $runtimePath 'cuda\bin'
$licensePath = Join-Path $runtimePath 'licenses'
New-Item -ItemType Directory -Force -Path $cudaBin, $licensePath | Out-Null
foreach ($name in $manifest.native.cuda_dlls) {
    Copy-Item -LiteralPath (Join-Path $cudaPath "bin\$name") -Destination $cudaBin -Force
}
Copy-Item -LiteralPath (Join-Path $cudaPath 'EULA.txt') -Destination (Join-Path $licensePath 'CUDA-12.8-EULA.txt') -Force
Copy-Item -LiteralPath (Join-Path $llamaSource 'LICENSE') -Destination (Join-Path $licensePath 'llama.cpp-LICENSE') -Force
$installedLicenses = Join-Path $VisualStudioRoot 'Licenses\2052'
Copy-Item -LiteralPath (Join-Path $installedLicenses 'Redist.txt') -Destination (Join-Path $licensePath 'Microsoft-REDIST.txt') -Force
Copy-Item -LiteralPath (Join-Path $installedLicenses 'ThirdPartyNotices.txt') -Destination (Join-Path $licensePath 'Microsoft-ThirdPartyNotices.txt') -Force
foreach ($item in $manifest.supplemental_licenses) {
    $sourceLicense = Join-Path $projectRoot $item.source_path
    if ((Get-Item -LiteralPath $sourceLicense).Length -ne $item.size -or
        (Get-FileHash -LiteralPath $sourceLicense -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.sha256) {
        throw "Pinned supplemental license checksum verification failed: $($item.component)"
    }
    Copy-Item -LiteralPath $sourceLicense -Destination (Join-Path $runtimePath $item.runtime_path) -Force
}

foreach ($item in $manifest.models.files) {
    $relative = [string]$item.path
    $modelSource = if ($relative.StartsWith('FireRedVAD-ONNX/')) {
        Join-Path $sourcePath ('models\' + $relative)
    } else { Join-Path $sourcePath ('models\Confucius4-R2T2-GGUF\' + $relative) }
    if (-not (Test-Path -LiteralPath $modelSource -PathType Leaf) -or
        (Get-Item -LiteralPath $modelSource).Length -ne $item.size -or
        (Get-FileHash -LiteralPath $modelSource -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.sha256) {
        throw "Pinned source model/license checksum verification failed: $relative"
    }
    $target = Join-Path $modelPath $relative
    New-Item -ItemType Directory -Force -Path (Split-Path $target -Parent) | Out-Null
    if (Test-Path -LiteralPath $target) {
        if ((Get-Item -LiteralPath $target).Length -ne $item.size -or
            (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.sha256) {
            # An existing target might be a hard link; never overwrite source weights through it.
            throw "Existing model asset differs from the pin; use a fresh model directory: $target"
        }
        continue
    }
    $linked = $false
    if (-not $CopyModels -and $item.size -ge 1MB -and
        [IO.Path]::GetPathRoot($modelSource).Equals([IO.Path]::GetPathRoot($target), [StringComparison]::OrdinalIgnoreCase)) {
        try {
            New-Item -ItemType HardLink -Path $target -Value $modelSource -ErrorAction Stop | Out-Null
            $linked = $true
        } catch { Write-Verbose "Hard link unavailable; copying $relative." }
    }
    if (-not $linked) { Copy-Item -LiteralPath $modelSource -Destination $target }
}

$portablePython = Join-Path $pythonPath 'python.exe'
& $portablePython -I -B (Join-Path $PSScriptRoot 'verify_confucius_runtime.py') `
    --project-root $projectRoot --runtime-root $runtimePath --model-root $modelPath `
    --write-inventory --cuda-architectures ($builtArchitectures -join ';')
if ($LASTEXITCODE -ne 0) { throw 'Portable Confucius runtime verification failed.' }
Write-Host "Portable Confucius runtime ready: $runtimePath"
Write-Host "Pinned Confucius Q8 models ready: $modelPath"
