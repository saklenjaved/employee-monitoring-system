# Serves the Django API over HTTPS on this computer, for the Windows agent.
# Run from anywhere: .\monitoring-platform\scripts\run_https.ps1
# Use it in a second PowerShell window next to "python manage.py runserver" (plain HTTP, for the browser).
# Stop with Ctrl+C. The script also stops the Hypercorn process tree it started.

$ErrorActionPreference = "Stop"
$Server = Split-Path -Parent $PSScriptRoot          # ...\server
$Root = Split-Path -Parent $Server                   # ...\windows-agent
$Python = Join-Path $Server ".venv\Scripts\python.exe"
$Certs = Join-Path $Root "monitoring-platform\certs"
$Cert = Join-Path $Certs "cert.pem"
$Key = Join-Path $Certs "key.pem"
$Port = 8443

if (-not (Test-Path $Python)) { throw "Virtual environment not found. Create it in monitoring-platform\.venv first (see monitoring-platform\README.md)." }

$openssl = (Get-Command openssl -ErrorAction SilentlyContinue).Source
if (-not $openssl) { $openssl = "C:\Program Files\Git\usr\bin\openssl.exe" }

# Create the local CA and server certificate when they are missing. Recreate them when the server
# certificate is not signed by the local CA (an old self-signed one), or expires within 7 days.
$CaCert = Join-Path $Certs "rootCA.pem"
$needsCert = -not ((Test-Path $Cert) -and (Test-Path $Key) -and (Test-Path $CaCert))
if (-not $needsCert -and (Test-Path $openssl)) {
    & $openssl verify -CAfile $CaCert $Cert *> $null
    $needsCert = ($LASTEXITCODE -ne 0)
    & $openssl x509 -in $Cert -noout -checkend 604800 *> $null
    if ($LASTEXITCODE -ne 0) { $needsCert = $true }
    # A certificate without an Authority Key Identifier is refused by the agent (Python 3.13 and newer).
    if (-not (& $openssl x509 -in $Cert -noout -text | Select-String "Authority Key Identifier")) { $needsCert = $true }
}
if ($needsCert) {
    Write-Host "Creating the local development certificate authority and server certificate..."
    & (Join-Path $Root "scripts\make_demo_cert.ps1")
    if ($LASTEXITCODE -ne 0) { throw "Creating the local certificate failed." }
}

# Refuse to start while anything listens on the port. Windows lets a second listener bind beside an
# old one, and then requests can reach the old server (with its old certificate). Ghost entries left by
# exited processes need a reboot or a sign-out to clear.
$owners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty OwningProcess -Unique)
if ($owners.Count -gt 0) {
    throw "Port $Port already has a listener (process id(s): $($owners -join ', ')). Stop those processes; if no process is shown in Task Manager, sign out or restart Windows, then run this script again."
}

$env:DJANGO_DEBUG = "1"   # development settings; this server is for local agent tests only
Write-Host "Serving the API at https://127.0.0.1:$Port/api/agent/v1/ (Ctrl+C to stop)"
Push-Location $Server
try {
    & $Python -m hypercorn config.asgi:application --certfile $Cert --keyfile $Key --bind "127.0.0.1:$Port"
}
finally {
    Pop-Location
    # Stop the process that still holds the port, with its children (Hypercorn can leave a worker behind).
    $owners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique
    foreach ($processId in $owners) {
        & taskkill /PID $processId /T /F *> $null
    }
}
