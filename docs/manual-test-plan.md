# Manual test plan (Windows)

Run these on your own Windows 10 or 11 PC (64-bit). Tick each item and note the result. Automated tests run with `pytest` and cover the logic; these checks cover the real Windows behaviour.

Setup first: follow README.md (venv, requirements, mock backend, `config.toml`).

| ID | Check | Steps | Expected | Result |
|---|---|---|---|---|
| M01 | Install | Run `pip install -r requirements.txt` in the venv | No errors | |
| M02 | Registration | `python -m winagent register --config config.toml` | "device registered"; `demo_data\credential.bin` exists and does not contain the token in text | |
| M03 | Sign-in start (optional) | `python -m winagent autostart enable --config config.toml`, then sign out and in | Agent starts; tray icon appears | |
| M04 | Tray icon | Start `python -m winagent run --config config.toml` | Tray icon shows "Monitoring active \| Connected" | |
| M05 | App and window capture | Open Notepad, then a normal document window | `app_focus` events show `notepad.exe` and the title (check in mock DB with `python -m mock_backend.show`) | |
| M06 | Excluded app | Open KeePass if installed, or rename a test exe to `KeePass.exe` and run it | No `app_focus` events for that app | |
| M07 | Idle transition | Leave the PC untouched for more than 5 minutes (or set `idle_threshold_seconds = 60` for a quick test) | `activity_sample` events change to `state: idle` | |
| M08 | Activity resumes | Move the mouse after the idle period | `state` returns to `active` within one sample | |
| M09 | Browser capture | Open Chrome or Edge | Expected: app name only. URL capture is a STUB (not implemented) | |
| M10 | Screenshot off | Default config | No `screenshots` rows in the mock DB, no PNG in `mock_backend\data\storage` | |
| M11 | Screenshot on | Set `screenshots.enabled = true` in `config.toml` AND `"enabled": true` in `mock_backend\demo_policy.json`, restart mock and agent, wait 10 min (or lower `interval_seconds` to 60 for a quick test) | One PNG in `mock_backend\data\storage`, one `completed` row, and no leftover PNG in `demo_data	mp` | |
| M12 | Screenshot blur | Set a blur region in the policy | The stored PNG has the region filled | |
| M13 | Monitoring hours | Set `start`/`end` in the policy to a window that is closed now | Health shows `paused_hours`; no new events | |
| M14 | Offline and reconnect | Stop the mock (Ctrl+C); wait 2 minutes; restart it | Health shows "Not connected" while stopped; after restart the queue drains and the backend count rises with no duplicates | |
| M15 | Remote session | Connect with Remote Desktop to the PC from another device (or log on through RDP) | Log shows `session type rdp ... no monitoring`; no events, no screenshots | |
| M16 | CPU and memory | Run `python scripts\measure_usage.py --process python.exe --seconds 120` while the agent runs | Low CPU; memory well under 150 MB | |
| M17 | Uninstall | `python -m winagent autostart disable --config config.toml`; stop the agent; delete the folder | No `WinAgent` value in `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` | |
| M18 | Log hygiene | Open `demo_data\logsgent.log` | No tokens, no `Bearer`, no signed URLs, no screenshot content | |
