# TAD coverage

Agent-side items from TAD sections 1, 4, 5, 6, 7, 8, 9, 10 and 11. Gaps are marked **STUB** or **GAP**.

| TAD section | Requirement | File(s) | Test(s) | Status |
|---|---|---|---|---|
| 1.1 | Responsibilities (registration, collection, screenshots, sync, status) | `winagent/*` | `test_e2e.py`, unit tests | Done |
| 1.2 | Out of scope (recording, webcam, mic, keystrokes) | none written | `test_privacy_static.py`, `test_contracts.py` | Done |
| 4 | Where each part runs | `api_client.py`, `http_client.py`, `screenshots/uploader.py` | `test_registration.py`, `test_screenshot_manager.py` | Agent side done. Backend is the mock (no PostgreSQL, no real S3) |
| 5.1 | Launcher | `__main__.py`, `launcher.py`, `bootstrap.py`, `config.py` | `test_launcher.py`, `test_config.py` | Done |
| 5.1 | Registration and auth (DPAPI credential) | `registration.py`, `credentials.py`, `platform_win.py` | `test_registration.py`, `test_credentials.py`, `test_windows_manual.py` (M01) | Done |
| 5.1 | Application monitor (foreground app and title) | `monitors/app_monitor.py`, `platform_win.py` | `test_app_monitor.py`, `test_window_titles.py`, manual M05 | Done |
| 5.1 | Website monitor | `monitors/website_monitor.py` | `test_activity_monitor.py` (stub test) | **STUB.** Interface and stub only. See the stubs list |
| 5.1 | Activity monitor (GetLastInputInfo) | `monitors/activity_monitor.py`, `platform_win.py` | `test_activity_monitor.py`, manual M07, M08 | Done |
| 5.1 | Screenshot manager | `screenshots/*` | `test_screenshot_manager.py`, `test_e2e.py`, manual M10 to M12 | Done. Blur is a solid fill (placeholder) |
| 5.1 | Sync manager (batches, retry, dedupe) | `sync.py`, `queue.py`, `sqlite_queue.py` | `test_sync.py`, `test_queue.py`, `test_sqlite_queue.py`, `test_network_recovery.py` | Done |
| 5.1 | Local state store (config, credential, offline queue) | `config.py`, `credentials.py`, `sqlite_queue.py` | `test_sqlite_queue.py`, `test_credentials.py` | Done. Offline queue encrypted per event with DPAPI |
| 5.1 | Logging and health | `logging_setup.py`, `health.py` | `test_logging.py`, `test_website_and_health.py` | Done |
| 6 | When monitoring runs (hours, sampling, screenshot frequency) | `launcher.py`, `schedule.py`, `agent_policy.py` | `test_launcher.py` | Done |
| 7 | Installation and first-time setup | `__main__.py` (register, autostart), `build.ps1` | `test_release_and_install.py`; manual M01 to M03, M17 | Partial. No installer; per-user zip and optional Run key. Signing placeholder |
| 8 | Activity event flow | `events.py`, `sync.py`, `api_client.py` | `test_contracts.py`, `test_sync.py` | Done |
| 9 | Screenshot upload flow | `screenshots/manager.py` | `test_screenshot_manager.py`, `test_e2e.py` | Done |
| 10 | Six API capabilities | `api_client.py`, `mock_backend/server.py`, `docs/api-contract.yaml` | `test_mock_backend.py`, `test_contracts.py`, `test_e2e.py` | Done with the mock. The contract is proposed, not confirmed by a real backend |
| 11 | Security, privacy, reliability | `http_client.py`, `credentials.py`, `logging_setup.py`, `sqlite_queue.py` | `test_registration.py`, `test_logging.py`, `test_privacy_static.py`, `test_network_recovery.py` | Done. **GAP:** the in-memory queue is lost on exit |

## Stubs and gaps

1. **STUB: website tracking.** Chrome and Edge window titles show the page title, not the URL. Reading the URL reliably needs a browser extension or UI Automation, which is more than this POC. The interface exists and is wired in, and it emits nothing.
2. **Placeholder: blur.** Solid fill over configured regions (TAD 13 open).
3. **No installer.** Per-user zip, optional HKCU Run key (TAD 13 open).
4. **Placeholder: code signing** in `build.ps1`.
5. **Mock backend.** No PostgreSQL and no AWS S3. Upload goes through the same HTTP function that will take a real pre-signed URL.
6. **GAP: in-memory queue** is lost when the process exits (TAD 11). The SQLite queue covers network outages while the agent keeps running.
