# Builds the per-user Windows agent with PyInstaller (TAD 12, release).
# Usage (from the project root, after creating .venv and installing with pip install -e ".[dev]"):
#   powershell -ExecutionPolicy Bypass -File scripts\build_pyinstaller.ps1
#
# Output: dist\winagent\ (one-folder build) and dist\winagent-<version>.zip
# Installer technology is an OPEN decision (TAD 13). This script produces a zip for now.
# Code signing is a PLACEHOLDER. No certificate is included and nothing is signed here.

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { throw "Create the virtualenv first: py -3.11 -m venv .venv" }

$Version = & $Python -c "import winagent; print(winagent.__version__)"
Write-Host "Building winagent $Version"

# 1. Tests must pass before a release build (CI-safe subset; manual tests are excluded).
& $Python -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "tests failed; build stopped" }

# 2. PyInstaller one-folder build. No admin rights are needed to run it.
& $Python -m PyInstaller --noconfirm --clean --onedir --name winagent `
    --paths src --hidden-import win32timezone `
    --collect-submodules winagent `
    scripts\entry.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

# 3. SIGNING PLACEHOLDER (open decision, TAD 13). Replace with the approved signtool step.
# Expected future form: signtool sign /fd SHA256 /tr <timestamp-url> /td SHA256 /f <cert.pfx> dist\winagent\winagent.exe
if ($env:WINAGENT_SIGN_CERT) {
    Write-Warning "WINAGENT_SIGN_CERT is set, but signing is not implemented in the POC. The build is UNSIGNED."
} else {
    Write-Warning "Code signing placeholder: build is UNSIGNED. Do not distribute to employees."
}

# 4. Package the output with its version in the name.
$Zip = Join-Path $Root "dist\winagent-$Version.zip"
if (Test-Path $Zip) { Remove-Item $Zip }
Compress-Archive -Path "dist\winagent\*" -DestinationPath $Zip
Write-Host "Built $Zip (UNSIGNED placeholder)"
