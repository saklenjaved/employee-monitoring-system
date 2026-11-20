# Implementation plan: Windows Desktop Monitoring Agent POC

> Note: this is the plan from the first build. The current layout is `winagent/` at the project root (not `src/`). The current defaults are in `docs/open-decisions.md`, and coverage is in `docs/tad-coverage.md`.

Source of truth: `Windows_Agent_Technical_Architecture_Document (1).docx` (TAD). Section numbers below refer to the TAD.
Owner column names the `.claude/skills` or `.claude/agents` that governs the work.

## Step 1 inventory (what existed when the POC started)

| Item | Status |
|---|---|
| `CLAUDE.md` | Present. Matches TAD. |
| `.claude/rules/` (5 files) | Present. Matches TAD. No conflicts. |
| `.claude/skills/` (3 skills) | Present. |
| `.claude/agents/agent-reviewer.md` | Present. |
| `docs/` | Missing. Created. |
| `src/`, `tests/`, `pyproject.toml` | Missing. Created. |

Conflicts with the TAD: none blocking. Two notes:

1. `CLAUDE.md` says open TAD 13 items must not be implemented without asking. The POC defaults supplied by the requester settle several of them (see `open-decisions.md`). Those are implemented as defaults, not as approvals.
2. The offline queue is in-memory in this POC. TAD 11 and `.claude/rules/sync-and-idempotency.md` both expect a bounded queue. This is recorded as a gap, not hidden.

## Module map

| TAD section | Component | Module(s) | Owning skill / agent | Tests |
|---|---|---|---|---|
| 5.1 Agent launcher | Launcher, monitoring-hours enforcement, status | `launcher.py`, `__main__.py`, `tray.py` | agent-reviewer | `test_launcher.py`, `test_monitoring_hours.py` |
| 5.1 Device registration & auth | Register, store credential | `registration.py`, `credentials.py`, `http_client.py` | `security-device-auth` rule, agent-reviewer | `test_registration.py`, `test_credentials.py`, `test_http_client.py` |
| 5.1 Application monitor | Foreground app/window | `monitors/app_monitor.py`, `platform_win.py` (`ForegroundWindowReader`) | `add-agent-monitor` | `test_app_monitor.py` |
| 5.1 Website monitor | Interface and stub only | `monitors/website_monitor.py` | `add-agent-monitor` | `test_website_monitor.py` |
| 5.1 Activity monitor | Idle/active via GetLastInputInfo | `monitors/activity_monitor.py`, `platform_win.py` (`IdleTimeReader`) | `privacy-no-keystrokes` rule | `test_activity_monitor.py`, `test_privacy_static.py` |
| 5.1 Screenshot manager | Policy, capture, redact, authorize, upload, complete | `screenshots/manager.py`, `screenshots/policy.py`, `screenshots/redact.py`, `screenshots/uploader.py` | `screenshot-flow` skill, `screenshot-policy` rule | `test_screenshot_manager.py`, `test_screenshot_policy.py`, `test_redact.py`, `test_uploader.py` |
| 5.1 Sync manager | Batches ≤500, backoff, dedupe by event_id | `sync.py`, `queue.py` | `sync-and-idempotency` rule | `test_sync.py`, `test_queue.py`, `test_network_recovery.py` |
| 5.1 Local state store | Config (TOML) and DPAPI credential | `config.py`, `credentials.py` | `security-device-auth` rule | `test_config.py`, `test_credentials.py` |
| 5.1 Logging & health | Redacting logs, health file, heartbeat | `logging_setup.py`, `health.py` | `testing-and-debugging` skill | `test_logging.py`, `test_health.py` |
| 6 When monitoring runs | Sampling intervals, hours | `launcher.py`, `policy.py` | — | `test_monitoring_hours.py` |
| 7 Installation & first-time setup | Install, autostart (HKCU Run), register | `__main__.py`, `registration.py`, `build.ps1` | — | `test_release_and_install.py` (fakes), manual tests |
| 8 Activity event flow | Event creation → queue → batch POST | `events.py`, `sync.py`, `monitors/*` | `add-agent-monitor` | `test_events.py`, `test_event_schema.py` |
| 9 Screenshot upload flow | Steps 1–7 of TAD 9 | `screenshots/manager.py` | `screenshot-flow` skill | `test_screenshot_manager.py` |
| 10 API capabilities (6) | Client methods per endpoint | `api_client.py`, `docs/api-contract.yaml` | — | `test_api_contract.py`, `test_api_client.py` |
| 11 Security, privacy, reliability | Cross-cutting checks | `http_client.py`, `logging_setup.py`, `events.py` | agent-reviewer | `test_security.py`, `test_privacy_static.py` |
| 12 Testing, packaging, release | Build script, signing placeholder, version check | `build.ps1`, `pyproject.toml` | — | `test_release_and_install.py`, `docs/manual-test-plan.md` |

## Platform seam

All Windows-only calls live in `platform_win.py` behind small Protocols defined in `platform_base.py`:

- `ForegroundWindowReader.read() -> ForegroundInfo | None`
- `IdleTimeReader.idle_seconds() -> float`
- `ScreenCapturer.capture_png() -> bytes`
- `SessionInspector.session_type() -> str` and `is_locked() -> bool`
- `AutostartRegistry.enable(cmd) / disable()`
- `SecretProtector.protect(bytes) / unprotect(bytes)` (DPAPI)

Imports of `win32*`, `ctypes.windll` and `mss` happen inside functions, so the package imports and tests run on Linux with fakes.

## Event flow (TAD 8)

1. Monitor reads the signal (fake or Windows reader).
2. Monitor builds an `Event` (`events.py`): `event_id` (UUID4), `event_type`, `captured_at` (UTC ISO 8601), `payload`. No org or employee field.
3. `SyncManager.enqueue()` checks the event_id against queued ids and adds it to the bounded queue.
4. `SyncManager.flush()` sends batches of ≤500 to `POST /agent/v1/events/batch/`.
5. Server response `accepted` and `duplicate` remove events from the queue. `rejected` events are dropped and counted. Network errors keep the batch and back off.

## Screenshot flow (TAD 9)

`ScreenshotManager.run_once()` runs in this order and stops at the first failed check:

1. Screenshots enabled in the cached policy.
2. Inside monitoring hours.
3. Session type is `console`, and the device is not locked.
4. Foreground app not on the exclusion list. If it is, skip capture entirely.
5. Capture PNG.
6. Redact (solid-fill placeholder for policy regions).
7. Write to a private temp file, compute SHA-256.
8. `POST /agent/v1/screenshots/authorize/` returns a short-lived URL.
9. Upload with the injected `Uploader` (mocked in tests). Bytes do not go to Django.
10. `POST /agent/v1/screenshots/{id}/complete/`.
11. `finally:` delete the temp file, whether or not the upload succeeded.

Failures are isolated: a screenshot failure never blocks event sync (separate task).

## Build order (Step 4) and checkpoints

Each step ends with tests passing before the next step begins.

1. Config and DPAPI credential storage. Checkpoint: `test_config.py`, `test_credentials.py`.
2. Registration and auth against the contract. Checkpoint: `test_registration.py`, `test_http_client.py`.
3. App and activity monitors. Checkpoint: `test_app_monitor.py`, `test_activity_monitor.py`, `test_privacy_static.py`.
4. Website monitor stub. Checkpoint: `test_website_monitor.py`.
5. Sync manager and bounded queue. Checkpoint: `test_sync.py`, `test_network_recovery.py`.
6. Screenshot manager. Checkpoint: `test_screenshot_*.py`, `test_uploader.py`.
7. Launcher, monitoring hours, tray status. Checkpoint: `test_launcher.py`, `test_monitoring_hours.py`.
8. Logging, health, heartbeat. Checkpoint: `test_logging.py`, `test_health.py`.
9. PyInstaller script with code-signing placeholder. Checkpoint: `test_release.py`.

After each component: privacy and security review (agent-reviewer checklist, recorded in the component's review note inside `docs/test-plan.md`), then tests.

## Final check (Step 5)

`docs/tad-coverage.md` maps every TAD section to its files and tests and lists gaps and manual tests. The contract test checks the six endpoints. The schema test checks the event samples.
