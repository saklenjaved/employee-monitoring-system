<#
.SYNOPSIS
  Builds the Windows agent: PyInstaller exe, Inno Setup installer, optional code signing, and a copy for the server.

.DESCRIPTION
  Usage (from the project root, after creating .venv and installing with: pip install -e ".[dev]"):
    powershell -ExecutionPolicy Bypass -File build.ps1
    powershell -ExecutionPolicy Bypass -File build.ps1 -CheckOnly     # check tools and signing settings, build nothing
    powershell -ExecutionPolicy Bypass -File build.ps1 -SkipTests

  Outputs:
    dist\winagent\winagent.exe              the agent (one-folder build)
    dist\winagent-<version>.zip             the one-folder build, zipped
    dist\WinAgentSetup-<version>.exe        the installer
    monitoring-platform\agent_dist\WinAgentSetup.exe     the same installer, where the Admin Deployment page serves it

  Code signing (optional). Set these environment variables to sign winagent.exe and the installer:
    PFX_PATH        path to the code signing certificate (.pfx)
    PFX_PASSWORD    the certificate password (never printed)
    TIMESTAMP_URL   RFC 3161 timestamp server (default http://timestamp.digicert.com)
    SIGNTOOL_PATH   optional full path to signtool.exe (otherwise PATH and the Windows SDK are searched)
  With no PFX_PATH the build is unsigned and says so. With PFX_PATH set, any signing problem stops the build,
  so a build asked to be signed is never released unsigned by mistake.

  Other optional variables: ISCC_PATH (full path to ISCC.exe).
#>
[CmdletBinding()]
param(
    [switch]$SkipTests,
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
Set-Location $Root

function Stop-Build([string]$Message) {
    Write-Host "ERROR: $Message" -ForegroundColor Red
    exit 1
}

# Native tools write progress to stderr. That must not count as a failure, so only the exit code decides.
function Invoke-Native([string]$What, [scriptblock]$Command) {
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try { & $Command } finally { $ErrorActionPreference = $previous }
    if ($LASTEXITCODE -ne 0) { Stop-Build "$What failed (exit code $LASTEXITCODE)" }
}

function Find-Tool([string]$EnvName, [string]$ExeName, [string[]]$Candidates) {
    $fromEnv = [Environment]::GetEnvironmentVariable($EnvName)
    if ($fromEnv) {
        if (Test-Path -LiteralPath $fromEnv -PathType Leaf) { return $fromEnv }
        Stop-Build "$EnvName is set to '$fromEnv', but that file does not exist"
    }
    $onPath = Get-Command $ExeName -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    foreach ($pattern in $Candidates) {
        $found = Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue | Sort-Object FullName -Descending | Select-Object -First 1
        if ($found) { return $found.FullName }
    }
    return $null
}

# --- 1. Tools and settings ---------------------------------------------------------------------

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { Stop-Build "Create the virtualenv first: py -3.11 -m venv .venv" }

$Version = (& $Python -c "import winagent; print(winagent.__version__)").Trim()
if (-not $Version) { Stop-Build "Could not read the agent version" }
Write-Host "Building winagent $Version"

# The installer refuses to run when its built-in CA file is missing, and a silent install hides why.
# Check the path now, so a moved certificate stops the build instead of producing a broken installer.
if ($env:WINAGENT_CA_FILE -and -not (Test-Path -LiteralPath $env:WINAGENT_CA_FILE -PathType Leaf)) {
    Stop-Build "WINAGENT_CA_FILE is set to '$env:WINAGENT_CA_FILE', but that file does not exist"
}

Invoke-Native "PyInstaller check (pip install -e `".[dev]`")" { & $Python -m PyInstaller --version | Out-Null }

$Iscc = Find-Tool "ISCC_PATH" "ISCC.exe" @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
if (-not $Iscc) { Stop-Build "Inno Setup 6 (ISCC.exe) was not found. Install it or set ISCC_PATH." }

# The installer script has its own version number. A mismatch would give a wrong file name.
$IssText = Get-Content (Join-Path $Root "installer\winagent.iss") -Raw
if ($IssText -notmatch '#define AppVersion "([^"]+)"') { Stop-Build "installer\winagent.iss has no AppVersion" }
if ($Matches[1] -ne $Version) { Stop-Build "Version mismatch: agent is $Version, installer\winagent.iss is $($Matches[1])" }

$PfxPath = $env:PFX_PATH
$SigningRequested = [bool]$PfxPath
$Signtool = $null
$TimestampUrl = if ($env:TIMESTAMP_URL) { $env:TIMESTAMP_URL } else { "http://timestamp.digicert.com" }
if ($SigningRequested) {
    if (-not (Test-Path -LiteralPath $PfxPath -PathType Leaf)) { Stop-Build "PFX_PATH '$PfxPath' does not exist" }
    if (-not $env:PFX_PASSWORD) { Stop-Build "PFX_PATH is set but PFX_PASSWORD is not" }
    $Signtool = Find-Tool "SIGNTOOL_PATH" "signtool.exe" @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe",
        "$env:ProgramFiles\Windows Kits\10\bin\*\x64\signtool.exe"
    )
    if (-not $Signtool) { Stop-Build "signtool.exe was not found. Install the Windows SDK or set SIGNTOOL_PATH." }
    Write-Host "Code signing: ON (timestamp server $TimestampUrl)"
} else {
    Write-Warning "Code signing: OFF. No PFX_PATH set, so the build is UNSIGNED. Windows will warn employees. Do not distribute it widely."
}

if ($CheckOnly) {
    Write-Host "Check passed: PyInstaller ok, Inno Setup at $Iscc, version $Version."
    exit 0
}

function Sign-File([string]$File) {
    if (-not $SigningRequested) { return }
    Write-Host "Signing $File"
    # The password is passed to signtool and never printed.
    Invoke-Native "Signing $File" { & $Signtool sign /fd SHA256 /f $PfxPath /p $env:PFX_PASSWORD /tr $TimestampUrl /td SHA256 $File | Out-Null }
    Invoke-Native "Signature check for $File" { & $Signtool verify /pa /q $File }
}

# --- 2. Tests ----------------------------------------------------------------------------------

$Started = Get-Date
if ($SkipTests) {
    Write-Warning "Tests skipped (-SkipTests)."
} else {
    Invoke-Native "Tests" { & $Python -m pytest -q }
}

# --- 3. Agent executable -----------------------------------------------------------------------

Invoke-Native "PyInstaller" {
    # --windowed: no console window when Windows starts the agent at sign-in.
    & $Python -m PyInstaller --noconfirm --clean --onedir --windowed --name winagent `
        --paths src --hidden-import win32timezone `
        --collect-submodules winagent `
        --collect-data tzdata `
        scripts\entry.py
}
$AgentExe = Join-Path $Root "dist\winagent\winagent.exe"
if (-not (Test-Path $AgentExe)) { Stop-Build "PyInstaller finished but $AgentExe is missing" }
if ((Get-Item $AgentExe).LastWriteTime -lt $Started) { Stop-Build "$AgentExe is older than this build; PyInstaller did not rebuild it" }
if ((Get-Item $AgentExe).Length -lt 1MB) { Stop-Build "$AgentExe is suspiciously small" }
Sign-File $AgentExe

# --- 4. Installer ------------------------------------------------------------------------------

$Installer = Join-Path $Root "dist\WinAgentSetup-$Version.exe"
# WINAGENT_API_URL (https://...) is built into the installer as its default server address.
$IsccArgs = @("/Q")
if ($env:WINAGENT_API_URL) { $IsccArgs += "/DDefaultApiUrl=$($env:WINAGENT_API_URL)" }
# WINAGENT_CA_FILE (optional) is built in as the default CA file, for a private or demo certificate. It must exist on the PC.
if ($env:WINAGENT_CA_FILE) { $IsccArgs += "/DDefaultCaFile=$($env:WINAGENT_CA_FILE)" }
# WINAGENT_SCREENSHOTS=1 turns on the PC-side screenshot setting. The organization's policy must be on too. Off by default.
if ($env:WINAGENT_SCREENSHOTS -eq "1") { $IsccArgs += "/DDefaultScreenshots=1" }
Invoke-Native "Inno Setup" { & $Iscc @IsccArgs (Join-Path $Root "installer\winagent.iss") }
if (-not (Test-Path $Installer)) { Stop-Build "Inno Setup finished but $Installer is missing" }
if ((Get-Item $Installer).LastWriteTime -lt $Started) { Stop-Build "$Installer is older than this build; Inno Setup did not rebuild it" }
Sign-File $Installer

# --- 5. Copy for the server and package --------------------------------------------------------

$ServerDir = Join-Path $Root "monitoring-platform\agent_dist"
New-Item -ItemType Directory -Force -Path $ServerDir | Out-Null
$ServerCopy = Join-Path $ServerDir "WinAgentSetup.exe"
Copy-Item -LiteralPath $Installer -Destination $ServerCopy -Force
$InstallerHash = (Get-FileHash $Installer -Algorithm SHA256).Hash
if ((Get-FileHash $ServerCopy -Algorithm SHA256).Hash -ne $InstallerHash) { Stop-Build "The copy in monitoring-platform\agent_dist does not match the installer" }

$Zip = Join-Path $Root "dist\winagent-$Version.zip"
if (Test-Path $Zip) { Remove-Item $Zip }
Compress-Archive -Path "dist\winagent\*" -DestinationPath $Zip

$SignedText = if ($SigningRequested) { "SIGNED" } else { "UNSIGNED" }
Write-Host ""
Write-Host "Build complete ($SignedText)."
Write-Host "  Installer: $Installer"
Write-Host "  Server copy: $ServerCopy"
Write-Host "  SHA256: $InstallerHash"
