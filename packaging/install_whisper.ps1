param(
    [string]$Python = (Join-Path $PSScriptRoot '..\.runtime\python\python.exe')
)

$ErrorActionPreference = 'Stop'
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$requirements = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\requirements-whisper.txt')).Path
& $pythonPath -m pip install --break-system-packages --disable-pip-version-check --requirement $requirements
if ($LASTEXITCODE -ne 0) {
    throw "faster-whisper installation failed with exit code $LASTEXITCODE"
}
Write-Host 'Whisper transcription support is repaired. Restart Voice Studio.'
