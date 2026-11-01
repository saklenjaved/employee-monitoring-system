# Creates a self-signed certificate for the local mock backend (127.0.0.1 and localhost only).
# The agent trusts it through [api] ca_bundle, so TLS verification stays on.
# Needs openssl (Git for Windows ships one). The key and cert stay in mock_backend\certs (git-ignored).

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Certs = Join-Path $Root "mock_backend\certs"
New-Item -ItemType Directory -Force -Path $Certs | Out-Null

$openssl = (Get-Command openssl -ErrorAction SilentlyContinue).Source
if (-not $openssl) { $openssl = "C:\Program Files\Git\usr\bin\openssl.exe" }
if (-not (Test-Path $openssl)) { throw "openssl not found. Install Git for Windows or add openssl to PATH." }

& $openssl req -x509 -newkey rsa:2048 -nodes -days 30 `
    -keyout (Join-Path $Certs "key.pem") -out (Join-Path $Certs "cert.pem") `
    -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
if ($LASTEXITCODE -ne 0) { throw "openssl failed" }
Write-Host "Created $Certs\cert.pem and key.pem"
