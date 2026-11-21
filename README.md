# Windows Desktop Monitoring Agent (POC)

A simple Windows agent for the Employee Monitoring System, following the agent TAD in `docs/TAD-windows-agent.md` (the platform TAD is `docs/TAD.md`). It runs on your PC and talks to a small local mock backend over HTTPS. Nothing leaves your machine.

**Status:** proof of concept. Several TAD 13 items are still open (see `docs/open-decisions.md`). Code signing is a placeholder.

## What it does

- Registers the device with a registration code. The credential is encrypted with Windows DPAPI.
- Reads the monitoring policy from the backend (hours, sampling, screenshot settings, exclusions).
- Records the foreground app and window title (pywin32). Sampling every 30 s.
- Records active and idle time from `GetLastInputInfo` (idle after 300 s). It never reads what you type.
- Sends events in batches of at most 500 over HTTPS, with retries and unique event IDs.
- Keeps events in an encrypted SQLite queue (max 10,000) when the network is down.
- Screenshots: OFF by default. When on, every 10 minutes, after the policy, exclusion and hours checks pass. Blurred, uploaded, local file deleted.
- Heartbeat with version, last sync, connection state and errors.
- Tray icon shows "Connected / Not connected" and the monitoring state.
- Only in a console session. RDP, Citrix and VDI sessions are logged by type and not monitored.

## Requirements

- Windows 10 or 11, 64-bit.
- Python 3.11 or newer. Check with `py -0`.
- OpenSSL (Git for Windows includes one) to make the local demo certificate.

## 1. Set up (PowerShell, in the project folder)

```powershell
cd D:\my_projects\windows-agent
py -3.14 -m venv .venv                 # or: py -3.11 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m pip install -e .
```

## 2. Create the local certificate and config (once)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\make_demo_cert.ps1
copy config.example.toml config.toml
```

`config.toml` already has the test registration code `DEMO-LOCAL-0001` and points at the local certificate. Screenshots stay off unless you enable them (section 6).

## 3. Start the mock backend (terminal 1, leave it running)

```powershell
.venv\Scripts\python -m mock_backend.server --cert monitoring-platform\certs\cert.pem --key monitoring-platform\certs\key.pem
```

It prints `Mock backend listening on https://127.0.0.1:8443/api/agent/v1/`. Stop it with Ctrl+C.

## 4. Register and run the agent (terminal 2)

```powershell
.venv\Scripts\python -m winagent register --config config.toml
.venv\Scripts\python -m winagent run --config config.toml
```

You should see the tray icon. Its tooltip reads "Monitoring active | Connected".

Check status from a third terminal:

```powershell
.venv\Scripts\python -m winagent status --config config.toml
```

## 5. Check what arrived

```powershell
.venv\Scripts\python -m mock_backend.show --data-dir mock_backend\data
```

Events are in `mock_backend\data\backend.sqlite3`. Screenshots (if enabled) are PNG files in `mock_backend\data\storage`. Logs are in `demo_data\logs\agent.log`.

## 6. Turn screenshots on (to test)

1. In `config.toml`, set `[screenshots] enabled = true`. For a quick test, set `interval_seconds = 60`.
2. In `mock_backend\demo_policy.json`, set `"screenshots": {"enabled": true, ...}`.
3. Restart the mock and the agent.

Turn both back off afterwards.

## 7. Start at sign-in (optional, per user, no admin)

```powershell
.venv\Scripts\python -m winagent autostart enable --config config.toml
.venv\Scripts\python -m winagent autostart disable --config config.toml
```

It is OFF by default.

## 8. Tests (any OS, Windows calls are faked)

```powershell
.venv\Scripts\python -m pytest -q
```

Windows-only checks (run on your PC): `.venv\Scripts\python -m pytest -m windows_manual -v`. The full manual checklist is in `docs/manual-test-plan.md`.

## 9. Build (optional)

```powershell
powershell -ExecutionPolicy Bypass -File build.ps1
```

Produces `dist\winagent\` and an UNSIGNED zip. Code signing is a placeholder in `build.ps1`.

## Known limits

- Website (URL) tracking is a stub. See `docs/tad-coverage.md`.
- The in-memory queue is lost if the process exits. The SQLite queue covers network outages while the agent keeps running.
- The mock backend is for demonstration only. Its data is not protected like a real server.
- The S3 upload goes through `PresignedS3Uploader` in `winagent/screenshots/uploader.py`. The mock uses local storage through the same HTTP upload call.

## Layout

- `winagent/`: the agent (one file per responsibility).
- `mock_backend/`: the local mock backend and demo policy.
- `tests/`: pytest tests, including `test_e2e.py`.
- `docs/`: TAD (converted), open decisions, manual test plan, coverage map, ADRs, API contract, event schema.
- `build.ps1`, `scripts/`: build and helper scripts.
- `.claude/`, `CLAUDE.md`: project rules. Not changed.
