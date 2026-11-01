# Test plan

> Note: this is the plan from the first build. The current layout is `winagent/` at the project root (not `src/`). The current defaults are in `docs/open-decisions.md`, and coverage is in `docs/tad-coverage.md`.

Run the automated suite on any OS (Windows-only calls are faked):

```
.venv\Scripts\python -m pytest -q
```

Manual Windows tests run separately on a real device:

```
.venv\Scripts\python -m pytest -m windows_manual -v
```

Status key: **PASS** = run and passed. **FAKE** = automated with fakes; not proven on Windows. **MANUAL** = needs a real Windows device and is not yet run (or noted where it was).

## 1. Automated results (this POC, Python 3.14, Windows 11)

- `pytest -q`: **127 passed**, 6 deselected (manual).
- `pytest -m windows_manual`: **6 passed** on the dev machine (Windows 11 Pro 10.0.26300). Covers M01 to M06 below.
- PyInstaller build: succeeded. Frozen `winagent.exe --help` runs.

## 2. Coverage by TAD section 12 area

### Installation (TAD 12)
| Check | Status | Where |
|---|---|---|
| Config validation, defaults match POC decisions | FAKE | `test_config.py` |
| Build script gates on tests, has signing placeholder, no elevation | FAKE | `test_release_and_install.py` |
| Per-user only: HKCU Run key, DPAPI user scope | FAKE + PASS (M01, M06) | `test_release_and_install.py`, `test_windows_manual.py` |
| Unregister removes credential | FAKE | `test_release_and_install.py` |
| Clean install, upgrade, uninstall, startup behavior, supported Windows versions | MANUAL | M07 to M09 |

### Monitoring (TAD 12)
| Check | Status | Where |
|---|---|---|
| Foreground app event shape, title off by default, title truncation | FAKE | `test_app_monitor.py` |
| Idle vs active classification, threshold, clamping | FAKE | `test_activity_monitor.py` |
| Monitoring hours, weekends, midnight crossing, disabled window | FAKE | `test_launcher.py` |
| Sampling stops outside hours, RDP, not registered, auth failure | FAKE | `test_launcher.py` |
| Exclusion list applied from policy | FAKE | `test_launcher.py` |
| Real foreground window, idle time | PASS (M02, M03) | `test_windows_manual.py` |
| Timestamps UTC ISO 8601 with Z | FAKE | `test_contracts.py` |

### Website tracking (TAD 12)
| Check | Status | Where |
|---|---|---|
| Stub is unsupported and emits nothing | FAKE | `test_activity_monitor.py` (stub test) |
| Supported browsers, URL handling, exclusions, duplicate events | Not applicable (stub). Blocked by TAD 13 item 3 | — |

### Screenshots (TAD 12)
| Check | Status | Where |
|---|---|---|
| Off by default; local flag and server policy both needed | FAKE | `test_screenshot_manager.py` |
| Policy order: hours, session, lock, excluded app, all before capture | FAKE | `test_screenshot_manager.py` |
| Redaction before disk; authorize, direct upload, complete, delete in `finally` | FAKE | `test_screenshot_manager.py` (includes redaction tests) |
| Upload success and failure, reporting, no retry on rejection | FAKE (S3 mocked) | `test_screenshot_manager.py` |
| Presigned URL never appears in outcome or logs | FAKE | `test_screenshot_manager.py`, `test_logging.py` |
| Real capture returns PNG | PASS (M04) | `test_windows_manual.py` |
| Access audit trail | Backend. Not in agent | — |

### API and security (TAD 12)
| Check | Status | Where |
|---|---|---|
| Plain http rejected; TLS verify on for every call | FAKE | `test_registration.py`, `test_config.py` |
| 401 and 403 stop sync; no retry forever | FAKE | `test_sync.py`, `test_registration.py` |
| Token never in error messages or logs | FAKE | `test_registration.py`, `test_logging.py` |
| No `organization_id` or `employee_id` sent by agent; contract forbids them | FAKE | `test_contracts.py`, `test_registration.py`, `test_screenshot_manager.py` |
| Event schema rejects extra fields (typed text, org id) | FAKE | `test_contracts.py` |
| Tenant isolation, invalid device rejection, token expiry on the server | Backend. Needs backend tests | — |
| Payload validation | FAKE (schema) | `test_contracts.py` |
| Privacy: no keyboard hooks, raw input, clipboard, webcam, mic, recording APIs in source | FAKE (static scan) | `test_privacy_static.py` |

### Network recovery (TAD 12)
| Check | Status | Where |
|---|---|---|
| Connection drops retried with backoff, then recovered | FAKE | `test_network_recovery.py` |
| Lost response, resend with same event_id reported as duplicate | FAKE | `test_network_recovery.py` |
| Offline period keeps events; flushes on return | FAKE | `test_network_recovery.py` |
| Batches of at most 500 | FAKE | `test_sync.py` |
| Bounded queue, drop-oldest counted | FAKE | `test_queue.py` |
| Real Wi-Fi drop and sleep and resume | MANUAL | M10, M11 |

### Performance (TAD 12)
| Check | Status | Where |
|---|---|---|
| Queue bounded under 50,000 events | FAKE | `test_performance.py` |
| Flush of one day of events in bounded batches, under 2 s | FAKE | `test_performance.py` |
| Per-tick sampling cost under 1 ms (fakes) | FAKE | `test_performance.py` |
| CPU and memory on a real workstation over 8 hours; screenshot size | MANUAL | P1 to P3 |

### Release (TAD 12)
| Check | Status | Where |
|---|---|---|
| Version matches in pyproject and package | FAKE | `test_release_and_install.py` |
| Build script builds and packages | PASS (PyInstaller run on dev machine) | `build.ps1` |
| Code signing | Placeholder. Not verified. Open item | R2 |
| Staged rollout, update and rollback, health monitoring | Not implemented. Open (TAD 13 item 8) | R3 |

## 3. Manual test list (needs a real Windows device)

| ID | Test | Result |
|---|---|---|
| M01 | DPAPI round trip; blob does not contain plaintext | PASS (dev machine) |
| M02 | Foreground window returns an `.exe` name and no title by default | PASS (dev machine) |
| M03 | Idle time is non-negative and grows with no input | PASS (dev machine). Repeat with mouse movement to confirm reset |
| M04 | Screen capture returns PNG | PASS (dev machine) |
| M05 | Session type is `console` on the local desktop | PASS (dev machine) |
| M06 | Autostart enable and disable, HKCU only | PASS (dev machine) |
| M07 | Clean install on Windows 10 64-bit as a standard user, no UAC prompt | MANUAL |
| M08 | Clean install on Windows 11 64-bit, upgrade from 0.1.0 to 0.1.1, uninstall | MANUAL |
| M09 | Sign-in starts the agent; tray icon shows status; Quit stops it | MANUAL |
| M10 | Network drop for 10 minutes; events queue and send after return; no duplicates in backend | MANUAL |
| M11 | Sleep and resume; monitoring resumes; idle time correct after resume | MANUAL |
| M12 | Lock the screen; no capture and no activity events while locked | MANUAL |
| M13 | RDP session: agent logs session type, collects nothing | MANUAL |
| M14 | Monitoring hours: agent stops collecting at end time and resumes at start | MANUAL |
| M15 | Screenshot enabled on server: image captured, redacted, uploaded to S3 with the presigned URL, local file gone | MANUAL (needs backend and S3 test bucket) |
| M16 | Excluded app in foreground: no app event and no screenshot | MANUAL |

Performance and release manual items:

| ID | Test | Result |
|---|---|---|
| P1 | CPU below 2% average and memory below 150 MB over 8 hours | MANUAL |
| P2 | Screenshot file size at 1920x1080 PNG, measured | MANUAL |
| P3 | Event volume per day at 30 s sampling | MANUAL |
| R1 | Install on a clean VM with the zip, run `winagent register`, `run`, `status` | MANUAL |
| R2 | Code-signed build verifies with signtool | Blocked: no certificate (TAD 13 item 8) |
| R3 | Staged rollout, rollback, update | Blocked: not implemented (TAD 13 item 8) |

## 4. Privacy and security review (agent-reviewer checklist)

Reviewed against `.claude/agents/agent-reviewer.md`, component by component:

| Component | Checked | Result |
|---|---|---|
| Monitors (app, activity) | Keyboard hooks, typed text, clipboard, content capture | No findings. Static scan in `test_privacy_static.py` |
| Auth and HTTP | TLS verify, bearer header only, token not in errors | No findings |
| Credentials | DPAPI user scope, no plaintext fallback, repr hides token | No findings |
| Logging | Redacting filter covers Bearer, URLs with query strings, key=value secrets | No findings. Note: a message that contains a secret with an unusual format may not be caught. Keep messages to exception class names |
| Sync | Event ids UUID4, batch ≤500, backoff capped, no duplicates, auth stop | Fixed one bug: a batch with no acknowledgement looped forever. Now ends the flush (test added) |
| Screenshots | Policy before capture, redaction before disk, temp file deleted in `finally`, direct S3 | No findings. Note: on Windows `0o600` does not restrict access. The file sits in the per-user LOCALAPPDATA folder, which is the protection in this POC |
| Scope | Out-of-scope items, open TAD 13 items | Website tracking is a stub. Window titles off by default. Registration path is an assumption (see open-decisions) |

## 5. Known gaps (recorded, not hidden)

- In-memory queue only (TAD 11). See ADR 0003.
- Real backend not in the repo. The contract is proposed and not tested against the Django API.
- Blur is a solid-fill placeholder (TAD 13 item 5).
- No code signing, no update mechanism, no installer technology (TAD 13 items 2, 8).
