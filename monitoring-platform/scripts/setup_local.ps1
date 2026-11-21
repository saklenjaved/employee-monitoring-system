# Sets up the Django platform on this computer for development: virtual environment, packages,
# a local database with the migrations applied, and demo data.
# Run from the windows-agent folder: .\monitoring-platform\scripts\setup_local.ps1
#
# It never touches monitoring-platform\db.sqlite3, the database that holds real accounts. Migrate that one
# only after a backup and an explicit decision (see monitoring-platform\README.md).
#   -Fresh   delete the local database and build it again
#   -Test    run the test suite at the end

param(
    [switch]$Fresh,
    [switch]$Test,
    [string]$Database = "demo.sqlite3"
)

$ErrorActionPreference = "Stop"
$Server = Split-Path -Parent $PSScriptRoot          # ...\server
$Python = Join-Path $Server ".venv\Scripts\python.exe"
$DatabasePath = Join-Path $Server $Database

if ($Database -eq "db.sqlite3") {
    throw "Refusing to use db.sqlite3: it holds real accounts. Use the default demo.sqlite3 instead."
}

function Invoke-Python {
    # Django writes progress to stderr. Windows PowerShell would treat that as an error, so only the exit code counts here.
    $ErrorActionPreference = "Continue"
    & $Python @args
    if ($LASTEXITCODE -ne 0) { throw "Command failed: python $($args -join ' ')" }
}

Push-Location $Server
try {
    if (-not (Test-Path $Python)) {
        Write-Host "Creating the virtual environment..."
        py -3.14 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Could not create the virtual environment. Install Python 3.14 first." }
    }

    Write-Host "Installing packages..."
    Invoke-Python -m pip install --quiet -r requirements.txt

    # Development settings for this computer. The database path is set here, so it wins over .env.
    $env:DJANGO_DEBUG = "1"
    $env:SQLITE_PATH = $Database

    if ($Fresh -and (Test-Path $DatabasePath)) {
        Remove-Item $DatabasePath
        Write-Host "Deleted the old local database: $DatabasePath"
    }

    Write-Host "Applying migrations to $Database..."
    Invoke-Python manage.py migrate --noinput

    Write-Host "Loading demo data (safe to run again)..."
    Invoke-Python manage.py seed_demo

    if ($Test) {
        Write-Host "Running the tests..."
        Invoke-Python manage.py test
    }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "Ready. Start the server with:"
Write-Host "  cd server"
Write-Host "  .\.venv\Scripts\python.exe manage.py runserver"
Write-Host "Then open http://127.0.0.1:8000/ and sign in as admin@northwind.demo (password: demo-password-123)."
