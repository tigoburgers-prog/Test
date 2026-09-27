# Builds the Windows distribution: tests -> PyInstaller folder -> Inno Setup installer.
# Usage (PowerShell, from the repository root):  .\packaging\build_windows.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path .venv)) { py -3.11 -m venv .venv }
.\.venv\Scripts\python -m pip install --upgrade pip
.\.venv\Scripts\python -m pip install -r requirements-dev.txt

.\.venv\Scripts\python scripts\fetch_models.py
.\.venv\Scripts\python -m pytest
.\.venv\Scripts\pyinstaller packaging\traffic_snapshot.spec --noconfirm --distpath dist --workpath build

$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (Test-Path $iscc) {
    & $iscc packaging\installer.iss
    Write-Host "Installer: dist\TrafficSnapshot-Setup-*.exe"
} else {
    Write-Host "Inno Setup niet gevonden; alleen de map dist\TrafficSnapshot is gemaakt (direct startbaar)."
}
