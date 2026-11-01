# Open decisions

Every decision below is a **default, to confirm**. These defaults were chosen so the POC can run. They are not product approvals.

| # | Item | Default (to confirm) | Notes |
|---|---|---|---|
| 1 | Windows versions | Windows 10 and 11, 64-bit | Tested on Windows 11 only so far |
| 2 | Install and start | Per-user, HKCU Run key, no admin rights | `winagent autostart enable` turns it on. It is NOT enabled automatically |
| 3 | Idle threshold | 300 s | `monitoring.idle_threshold_seconds`, allowed 60..3600 |
| 3 | Sampling interval | 30 s | `monitoring.sample_interval_seconds`, allowed 5..300 |
| 4 | Screenshots | Off by default. When on, every 600 s, blur before upload, local file deleted after upload | Needs both the local config flag and the policy flag |
| 4 | Blur | Solid fill over configured regions (placeholder) | TAD 13 blur rules are still open |
| 5 | Devices per employee | One device per employee | Enforced by the backend, not by the agent |
| 6 | Updates | No auto-update | |
| 6 | Code signing | Placeholder comment in `build.ps1`. Build is unsigned | |
| 7 | Project folder | `winagent/` at the project root | |

## Other decisions made while building (also default, to confirm)

| Item | Default | Why |
|---|---|---|
| Registration | Registration code, sent over HTTPS. Local test code `DEMO-LOCAL-0001` in `config.example.toml` | TAD 7 leaves onboarding open |
| Window titles | On, with exclusions (excluded apps, sensitive title keywords) | Brief asks for window titles |
| Offline queue | Encrypted SQLite, max 10,000 events, max age 72 h, DPAPI per event | Brief asks for offline recovery |
| In-memory queue | Also used, for events not yet written to SQLite | Brief asks for both |
| Website tracking | Stub only. See `docs/tad-coverage.md` | No reliable URL source (see report) |
| Non-console sessions | RDP, Citrix and VDI: session type logged, nothing collected, no screenshots | TAD 13 open |
| Monitoring hours | Mon-Sun, 00:00-23:59 in the mock policy | Real policy comes from the backend |
| Mock certificate | Self-signed, for 127.0.0.1 and localhost, trusted only through `ca_bundle` | Verification stays on |

## Conflicts noted

- Earlier chat decisions were 10 s sampling and 5 min screenshots. The brief's defaults are 30 s and 10 min. This build uses the brief's defaults.
- Earlier chat said manual start only. The brief asks for sign-in start. This build keeps manual start as the default, and the autostart command is available.
- The brief asks for a `winagent/` folder at the root. This is what the build uses.

## Questions still open (TAD 13)

1. Registration: registration code (used here) or employee sign-in?
2. Installer technology (zip only for now).
3. Retention for failed screenshot uploads (this build deletes immediately).
4. Window titles: keep on, or limit them?
5. Encryption and retention rules for the offline queue (this build uses DPAPI and 72 h).
