# Runs "manage.py build_summaries" every 5 minutes, so the dashboards show new activity.
# The dashboards read daily totals, and this command builds them from the raw events.
#
#   .\monitoring-platform\scripts\schedule_summaries.ps1            create the task (runs as you, only while you are signed in)
#   .\monitoring-platform\scripts\schedule_summaries.ps1 -Remove    delete the task
#
# For a real server use the server's own scheduler (cron or a systemd timer) with the same command.

param([switch]$Remove)

$ErrorActionPreference = "Stop"
$TaskName = "WinAgent-BuildSummaries"
$Server = Split-Path -Parent $PSScriptRoot          # ...\server
$Python = Join-Path $Server ".venv\Scripts\python.exe"

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Removed the $TaskName task."
    exit 0
}

if (-not (Test-Path $Python)) { throw "Virtual environment not found. Create it in monitoring-platform\.venv first (see monitoring-platform\README.md)." }

# DJANGO_DEBUG=1 matches how the local development server runs. A real server uses its own environment instead.
$Command = "set DJANGO_DEBUG=1&& `"$Python`" manage.py build_summaries"
$Action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $Command" -WorkingDirectory $Server
$Trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
Write-Host "Created the $TaskName task. It runs build_summaries every 5 minutes while you are signed in."
Write-Host "Remove it with: .\monitoring-platform\scripts\schedule_summaries.ps1 -Remove"
