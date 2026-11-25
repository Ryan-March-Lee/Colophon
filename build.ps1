#Requires -Version 5.1
# Local build: dist\Colophon\ + dist\Colophon-windows-x64.zip (desktop window app)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

# Interpreter: activated conda env, then conda env "colophon", then legacy .venv, then PATH
$candidates = @()
if ($env:CONDA_PREFIX) { $candidates += (Join-Path $env:CONDA_PREFIX "python.exe") }
$conda = Get-Command conda -ErrorAction SilentlyContinue
if ($conda) {
    $condaBase = & conda info --base 2>$null
    if ($condaBase -and (Test-Path $condaBase)) {
        $candidates += (Join-Path $condaBase "envs\colophon\python.exe")
    }
}
$candidates += (Join-Path $PSScriptRoot ".venv\Scripts\python.exe")

$python = "python"
foreach ($candidate in $candidates) {
    if (Test-Path $candidate) { $python = (Resolve-Path $candidate).Path; break }
}

Write-Host "Using interpreter: $python"
& $python -m pip install --disable-pip-version-check -q -r requirements.txt pyinstaller
& $python -m PyInstaller --noconfirm --clean Colophon.spec

Copy-Item "packaging\HOW-TO-USE.txt" "dist\Colophon\HOW-TO-USE.txt" -Force

$zip = "dist\Colophon-windows-x64.zip"
if (Test-Path $zip) { Remove-Item $zip }
Compress-Archive -Path "dist\Colophon\*" -DestinationPath $zip

Write-Host ""
Write-Host "Done: $zip"
Write-Host "Send this zip to colleagues; they unzip it and double-click Colophon.exe."
