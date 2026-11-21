# Creates a local development certificate authority (CA) and a server certificate signed by it.
# The server certificate covers localhost, 127.0.0.1 and ::1. The Windows agent trusts rootCA.pem
# through [api] ca_bundle, and Chrome trusts it once it is added to the Windows certificate store.
# Development only. Production uses a certificate from a public or company CA.
#
# Files (monitoring-platform\certs, git-ignored, never commit):
#   rootCA.pem / rootCA-key.pem  the local CA. Keep the key private.
#   cert.pem / key.pem           the server certificate and its key (used by run_https.ps1).
#
# Needs openssl (Git for Windows ships one).

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Certs = Join-Path $Root "monitoring-platform\certs"
New-Item -ItemType Directory -Force -Path $Certs | Out-Null

$openssl = (Get-Command openssl -ErrorAction SilentlyContinue).Source
if (-not $openssl) { $openssl = "C:\Program Files\Git\usr\bin\openssl.exe" }
if (-not (Test-Path $openssl)) { throw "openssl not found. Install Git for Windows or add openssl to PATH." }

$CaCert = Join-Path $Certs "rootCA.pem"
$CaKey = Join-Path $Certs "rootCA-key.pem"
$Cert = Join-Path $Certs "cert.pem"
$Key = Join-Path $Certs "key.pem"
$Csr = Join-Path $Certs "server.csr"
$Ext = Join-Path $Certs "server-ext.cnf"

# 1. The local CA (10 years is fine for a development CA). An existing CA is kept, so a CA that is already
#    trusted in Windows stays valid when only the server certificate is renewed.
if ((Test-Path $CaCert) -and (Test-Path $CaKey)) {
    Write-Host "Keeping the existing local CA ($CaCert)."
} else {
    & $openssl req -x509 -newkey rsa:2048 -nodes -days 3650 `
        -keyout $CaKey -out $CaCert `
        -subj "/CN=Employee Monitoring Local Dev CA" `
        -addext "keyUsage=critical,keyCertSign,cRLSign"
    # Note: -x509 already adds basicConstraints CA:TRUE. Adding it again with -addext makes OpenSSL 1.1.1
    # ignore the CA when it checks a leaf certificate.
    if ($LASTEXITCODE -ne 0) { throw "openssl failed creating the CA" }
}

# 2. The server certificate: names it must match, and what it may be used for.
Set-Content -Path $Ext -Encoding ascii -Value @(
    "basicConstraints=CA:FALSE",
    "keyUsage=digitalSignature,keyEncipherment",
    "extendedKeyUsage=serverAuth",
    # Python 3.13 and newer (the agent) refuse a server certificate without these identifiers.
    "subjectKeyIdentifier=hash",
    "authorityKeyIdentifier=keyid,issuer",
    "subjectAltName=DNS:localhost,IP:127.0.0.1,IP:0:0:0:0:0:0:0:1"
)
& $openssl req -new -newkey rsa:2048 -nodes -keyout $Key -out $Csr -subj "/CN=localhost"
if ($LASTEXITCODE -ne 0) { throw "openssl failed creating the server request" }
# 398 days is the longest validity browsers accept for a server certificate.
& $openssl x509 -req -in $Csr -CA $CaCert -CAkey $CaKey -CAcreateserial -out $Cert -days 398 -extfile $Ext
if ($LASTEXITCODE -ne 0) { throw "openssl failed signing the server certificate" }
Remove-Item -Force $Csr, $Ext -ErrorAction SilentlyContinue

& $openssl verify -CAfile $CaCert $Cert
if ($LASTEXITCODE -ne 0) { throw "The server certificate does not verify against the local CA" }
# cert.pem holds the full chain: the server certificate, then the local CA. The server sends both, and an agent
# installed with ca_bundle = cert.pem (older installers) finds the CA in that file, so it keeps verifying.
[System.IO.File]::AppendAllText($Cert, [System.IO.File]::ReadAllText($CaCert))
Write-Host "Created the local CA ($CaCert) and the server certificate ($Cert)."
Write-Host "Trust the CA once in Windows: certutil -user -addstore Root `"$CaCert`""
