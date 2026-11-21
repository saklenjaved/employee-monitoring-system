# Starts the development server on http://127.0.0.1:8000/ and refuses to start if another server already uses the port.
# Run from the windows-agent folder: .\monitoring-platform\scripts\start_local.ps1
#
# Why: on Windows a second "runserver" can share the port with an old one, and then some requests go to the
# old server (which may have old settings, for example mail). Stop the old server first.

$ErrorActionPreference = "Stop"
$Server = Split-Path -Parent $PSScriptRoot          # ...\server
$Python = Join-Path $Server ".venv\Scripts\python.exe"
$Port = 8000

if (-not (Test-Path $Python)) { throw "Virtual environment not found. Run .\monitoring-platform\scripts\setup_local.ps1 first." }

$listeners = netstat -ano | Select-String ":$Port\s" | Select-String "LISTENING"
if ($listeners) {
    $owner = ($listeners[0].Line.Trim() -split "\s+")[-1]
    throw "Port $Port is already in use by process $owner. Close the terminal that runs the old server, or stop that process, then run this script again."
}

Push-Location $Server
# The dashboards read daily totals, which build_summaries makes from the agents' raw events. Without it the
# dashboards show no usage even while agents send data. It runs in the background every minute while this
# server runs, and stops with it. (A real server uses its own scheduler; see schedule_summaries.ps1.)
$summaries = Start-Process -FilePath $Python -ArgumentList @("manage.py", "build_summaries", "--every", "60") `
    -WorkingDirectory $Server -WindowStyle Hidden -PassThru
Write-Host "Building dashboard totals every minute in the background (process $($summaries.Id))."
try {
    & $Python manage.py runserver "127.0.0.1:$Port"
}
finally {
    if ($summaries -and -not $summaries.HasExited) { Stop-Process -Id $summaries.Id -Force -ErrorAction SilentlyContinue }
    Pop-Location
}
