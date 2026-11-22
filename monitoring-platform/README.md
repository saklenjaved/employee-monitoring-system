# Employee Monitoring Platform: Django backend

Django modular monolith (TAD section 3). The Windows agent in the parent folder is a separate, finished component. It talks to this backend through the API described in `../docs/api-contract.yaml`.

Plan and phases: `../docs/roadmap.md`.

## Current status

Phases 0 to 6 are done: setup, accounts and access control, workforce and devices, event ingestion, classification with daily summaries, the four dashboards, and screenshots.

Phase 6 added screenshots (`screenshots` app). Nothing is captured until the Admin switches it on.
- Settings at `/screenshots/settings/`: on or off, interval (60 to 3600 seconds), retention (1 to 3650 days), and blur level. There are no defaults. The Admin must choose the interval, retention and blur level before the switch can be turned on. Managers may view screenshots by default. Download is off by default and needs view to be on.
- Exclusions at `/screenshots/exclusions/` (Admin only): a whole team or one employee. Excluded employees are not captured and their uploads are refused.
- Viewing at `/screenshots/`: the Admin sees the organization. A Manager sees only employees of their teams, and only when the Admin allows it. Employees see no screenshots. Every view and download is audited. Django never serves the image: the browser is redirected to a link that lasts 60 seconds, sent with no-store and no-referrer headers. Downloads are sent as an attachment with a file name.
- Upload (what the agent does): it asks `screenshots/authorize/`, where the server repeats every policy check. It then uploads straight to the private bucket with a link locked to one object, one size and one SHA-256 (the link lasts 5 minutes). Last, it reports `screenshots/<id>/complete/`, and the server checks the object size. Object keys are made by the server (`org-<id>/YYYY/MM/DD/<uuid>.png`). Images are never stored in the database.
- Cleanup: `python manage.py cleanup_screenshots` gives up uploads that are still unfinished after one hour (their object is deleted), then deletes screenshots older than each organization's retention period. Run it daily (not scheduled yet). Its audit entries hold counts only.
- `python manage.py init_storage` creates the bucket. Run it once after the S3 stand-in starts.

Local storage for development. The MinIO image cannot be pulled here, so `scripts/dev_s3.py` runs a local S3 emulator (moto) over HTTPS. Start it in its own terminal and leave it running:

```powershell
.\.venv\Scripts\python.exe scripts\dev_s3.py --cert certs\cert.pem --key certs\key.pem
```

It listens on `https://127.0.0.1:9100`, which is the development default for `S3_ENDPOINT_URL`. Its data is kept in memory, so it is empty after a restart. Stop it with `taskkill /PID <pid> /T /F`.

Known limits of Phase 6:
- Blur: the agent cannot blur whole screens yet. The blur level is stored and sent to the agent, but it has no effect until the agent is updated. The settings page says so.
- Real-agent check: with screenshots on in both the policy and the agent's local config, the agent uploaded two screenshots. Each stored size and SHA-256 matched what the agent announced, and the PNG header was valid. The Admin could view and download them, the Manager of the team could view them but not download them, and a Manager outside the team and the employee themself got no access. Views and downloads were audited.
- Production needs real bucket settings (`S3_BUCKET`, `S3_REGION`, `S3_ENDPOINT_URL`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_CA_BUNDLE`). The development keys are used only in development mode.

Phase 5 added the dashboards under `/dashboard/` (the link "Dashboard" in the header sends each role to its own page). Every page has a date filter (`?start=YYYY-MM-DD&end=YYYY-MM-DD`, at most 92 days; today in the organization's time zone by default):
- **Employee** (`/dashboard/me/`): own applications, websites, category split, active and idle time, activity percentage.
- **Manager** (`/dashboard/teams/`, `/dashboard/teams/<id>/`): the teams they belong to, with employee-wise figures. A Manager cannot open another team or an employee outside their teams.
- **Admin** (`/dashboard/organization/`): totals, top applications and websites, category split, team comparison, department comparison, employee table, agent status (online, offline, revoked), and the number of unclassified items.
- **Super Admin** (`/dashboard/platform/`): organizations (active and inactive), employees, Admin accounts, agents online, per-organization counts, and recent administrative activity. Counts only. No employee monitoring data.
- Which of the nine usage signals are included: top applications, top websites, category split, time per application, time per website, activity percentage, and the per-team view. Usage trends and CSV export are P1 and stay in roadmap Phase 8.
- The bars are plain HTML and CSS, so there is no JavaScript library. The workday timeline and the employee's six widgets are Phase 7.

Fixes after the Phase 3 to 5 reviews:
- Event names are validated and window titles are dropped (above).
- Daily totals: the event lookup also filters by organization, `received_at` has an index, `build_summaries` loads employees in one query, and at most 300 applications and 300 websites are kept for an employee and day (the busiest).
- The Unclassified list now means: at least one employee who uses the item has no rule that applies to them (an organization rule, or a rule of one of their teams). Opening a dashboard no longer creates a policy row. The employee's email is no longer shown on their dashboard. A bad `Content-Length` gives a 400.
- A Manager's category figures use every rule that applies to the employees they see, including rules of teams the Manager is not in.

Still open from those reviews, each waiting for a decision: how long raw events and daily totals are kept (there is no cleanup command yet), the employee monitoring notice, whether viewing an employee's dashboard is audited, whether the server should refuse events outside the monitoring hours, and whether the platform page shows Admin emails.

Decisions marked "default, to confirm" (Phase 5):
- "Productive share" is productive time divided by all application time, with unclassified time in the total.
- An employee in two teams counts in both team rows. Department rows count each employee once.
- When the same application is classified differently for different employees, the combined list shows the category "Mixed".
- "Agents online" means a heartbeat within the last 15 minutes.
- The Super Admin's recent activity shows actions by Super Admins and Admins, not employee device events.
- "New or unapproved applications" is shown as the unclassified item count. "Unusual or risky domain access" is not built, because the PRD does not define what counts as risky.

Phase 4 added:
- `productivity`: classification rules (Productive, Unproductive, Neutral). A rule is for an application (exact file name) or a website (domain, which also covers subdomains), for the whole organization or for one team. Admin page: `/productivity/rules/`.
  - Which rule wins: a rule of the employee's team beats an organization rule. Then the most specific name wins, then the oldest rule.
  - Items with no rule are "unclassified". The Admin lists and classifies them at `/analytics/unclassified/`.
- `analytics`: daily totals built from the raw events by `python manage.py build_summaries` (run it on a schedule, for example every 5 minutes). It rebuilds every employee-day that received events recently, including late events from an agent's offline queue.
  - Every second is counted once. If samples overlap (a resent event, two devices in use at once), the overlap is credited once. Active time wins over idle time.
  - Websites are counted on their own and are not added to application time.
  - Categories are worked out when data is read, so a changed rule applies at once with no rebuild.
  - `analytics.services.usage_for(viewer, employee, start, end)` returns apps, websites, category totals, active and idle seconds and the activity percentage, or `None` if the viewer may not see that employee. Dashboards (Phase 5) use it.
- Monitoring settings now include the organization's time zone (default UTC, to confirm). A "day" is a calendar day in that zone.

Decisions marked "default, to confirm" (Phase 4):
- A sample covers the time before its timestamp: an event stamped 10:00:30 with 30 seconds covers 10:00:00 to 10:00:30.
- Application time counts all foreground time, including time when the employee was idle.
- Role-specific rules are not built. The PRD does not say whether "role" means the access role (Admin, Manager, Employee) or a job role, and only Employees are monitored.
- Unclassified usage is a separate bucket. Dashboards decide whether to show it as Neutral.
- Events captured more than an hour after midnight do not change the previous day.

Known agent issue (not changed, the agent is finished): the agent schedules its samples by the server's sample interval, but labels each event with the interval from its local config file, and it uses the local idle threshold instead of the server's. When the two differ, an event claims the wrong duration. The summaries still count each moment once, but the totals follow what the agent claims. They are correct when the local config and the policy match, which is the default (30 s and 300 s).

Phase 3 added `POST /api/agent/v1/events/batch/` and the `MonitoringEvent` model in `monitoring`:
- Every event is checked against `docs/event-schema.json`. Unknown fields (for example typed text, `organization_id`) are refused, and refused events are reported in `rejected`.
- Organization and employee come from the device record. The event ID is unique per device, so a resent event is stored once and reported in `duplicates`.
- Limits: 500 events and 2 MB per request (413 above that). These are defaults to confirm: a `captured_at` more than 10 minutes in the future or more than 30 days old is rejected, as are durations over 1 hour. A sample can cover at most one hour, and the daily totals look one hour past midnight to match.
- Window titles are checked but NOT stored. Titles can hold document names or mail subjects and nothing uses them. Storing them needs an organization setting and a retention limit first (open decision).
- An application name must be a plain file name (no path, no `/ \ : * ? " < > |`), and a website must be a valid host name. Anything else is rejected, so a name cannot carry other text.
- Each device may send 60 event batches a minute, and 30 policy or heartbeat calls a minute (429 above that). Like the registration limit, the counters are per process, so production needs a shared cache.
- Dashboards and totals come in Phase 4. Events are raw rows until then.
- Checked with the real agent: 20 events reached the backend, once each, under the right employee and organization, and the agent showed "Connected". An agent waits for its policy before it samples, so it did not queue events while the backend was down. The resend case is covered by the server tests.

Phase 2 added:
- `workforce`: departments, teams, Employees and Managers, and manager-team assignment. Admin pages under `/workforce/`.
- `devices`: single-use registration codes, devices, device credentials, and the agent API (`/api/agent/v1/`): `devices/register/`, `config/`, `heartbeat/`.
- `monitoring`: the per-organization monitoring policy (hours, idle threshold, sample interval). Admin page at `/monitoring/settings/`.
- The real Windows agent was checked against this backend: it registered with a code, downloaded the policy, and sent heartbeats.

All agent endpoints in the contract are built: events from Phase 3 and screenshots from Phase 6.

Phase 1 (below) added:

- `organizations`: the `Organization` model. The Super Admin can create, activate and deactivate organizations and create Admin accounts (pages under `/platform/organizations/`).
- `accounts`: the `User` model (email login, four roles, organization link), login, logout, password reset by email, and the user visibility rules in `services.py`.
- `access_control`: the `role_required` decorator. Every protected view uses it.
- `audit`: an append-only `AuditLog` and `record_event()`. Organization and Admin-account actions are recorded.
- Tests cover tenant isolation, role checks, login, password reset, and audit.

After the Phase 1 and 2 reviews (privacy and permission), these were fixed:
- Development mode now turns on only for `manage.py test` or `DJANGO_DEBUG=1`.
- Changes made in the Django admin site are audited, and users and organizations cannot be deleted there.
- Web login is limited to 10 failed attempts per email and 30 per IP address in 15 minutes. Password reset is limited to 10 requests per IP address and 3 emails per address per hour.
- Smaller fixes: mixed-case login, `no-store` on the credential response, a new registration code replaces earlier unused ones, heartbeat keeps optional values, atomic team creation, clearer audit notes, neutral "email cannot be used" message, and the contract lists the extra response codes.

Still open from the reviews, each waiting for a decision: whether the production deploy sits behind an HTTPS proxy (affects the registration limit and invite link scheme), whether the Django admin stays enabled in production, whether audit rows keep the actor's email, and the employee monitoring notice text.

Decisions marked "default, to confirm" (Phase 2):
- A registration code is valid for 7 days and works once. A device credential is valid for 30 days.
- Only people with the Employee role get devices. Managers and Admins do not.
- The default monitoring policy is Monday to Friday, 09:00 to 18:00, sample every 30 s, idle after 300 s.
- A device counts as online if its last heartbeat is less than 15 minutes old.
- Registration attempts are limited to 10 per hour per address. The counter lives in process memory, so production needs a shared cache.
- A Super Admin cannot see employee-level data or screenshots.

Decisions marked "default, to confirm" (Phase 1):
- Users of a deactivated organization cannot log in, and are logged out on their next request.
- The new Admin gets an 8-digit activation code by email. The code works once, expires after 24 hours, and locks after 5 wrong tries. The Admin enters it on the activation page (`/activate/`) and, if new, chooses a password there. No password is ever emailed or shown to the Super Admin. A new code replaces the unused one.
- No MFA, login rate limiting, or session timeout yet (session timeout and lockout rules are not defined in the PRD).

## Setup (Windows, PowerShell)

One command sets up everything for local development: the virtual environment (Python 3.14), the packages, a local database with all migrations applied, and demo data. Run it from the `windows-agent` folder:

```powershell
.\monitoring-platform\scripts\setup_local.ps1
```

Add `-Test` to run the test suite at the end, or `-Fresh` to delete the demo database and build it again. The script builds the demo data in `monitoring-platform\demo.sqlite3`. It does not touch `monitoring-platform\db.sqlite3`.

Then start the server. Use this script, which refuses to start while another server already uses port 8000:

```powershell
.\monitoring-platform\scripts\start_local.ps1
```

Run only one server at a time. On Windows, a second `runserver` can share the port with an old one, and then some requests reach the old server, which may still have old settings (for example old mail settings). If the script reports that port 8000 is in use, close the terminal of the old server first.

Password reset: the server allows three reset emails per address per hour. A fourth request is skipped, and the terminal shows `password reset skipped`. Restarting the server resets that counter. The terminal also shows `password reset email sent` for each email.

Open http://127.0.0.1:8000/ and sign in with your own account. The server uses `monitoring-platform\db.sqlite3`, which holds the real accounts (its three accounts were migrated on 2026-11-03; a backup is in `monitoring-platform\backups`).

To look at the demo data instead, start the server with its database set for that session only:

```powershell
$env:SQLITE_PATH = "demo.sqlite3"
.\.venv\Scripts\python.exe manage.py runserver
```

The demo accounts all use the password `demo-password-123`:

| Role | Email | What it shows |
|---|---|---|
| Super Admin | `super@platform.demo` | Platform page (organizations, counts only) |
| Admin | `admin@northwind.demo` | Organization dashboard, people, rules, settings |
| Manager | `amara.okafor@northwind.demo` | Dashboard of their own teams |
| Employee | `aisha.bello@contoso.demo` | Their own dashboard |

The demo data is fictional and lives only in `demo.sqlite3`.

`.env` sets `DJANGO_DEBUG=1`, so `manage.py` commands work from any shell. Use the `.\.venv\Scripts\python.exe` commands above in PowerShell. Do not use `set NAME=value` in PowerShell: it does nothing there. Use `$env:NAME = "value"` if you need a variable for one session.

### The database `db.sqlite3`

`monitoring-platform\db.sqlite3` holds the real accounts. It is migrated to the current code. Before any future migration, back it up first (copy the file to `monitoring-platform\backups`) and migrate it on purpose. The setup script refuses to use it.

## Run the tests

The setup script runs them with `-Test`. To run them by hand, the tests use their own temporary database and never touch `db.sqlite3`:

```powershell
.\.venv\Scripts\python.exe manage.py test
```

Run on PostgreSQL (what the TAD specifies):

```powershell
docker compose up -d db
$env:DJANGO_DEBUG = "1"
$env:DB_ENGINE = "postgres"
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py test
```

The Docker database is on host port 5433, so it does not clash with a PostgreSQL already installed on 5432. Stop it with `docker compose down`.

The Docker stack holds only PostgreSQL. The local S3 stand-in for screenshots is `scripts/dev_s3.py` (see Phase 6 above).

## Serve the API over HTTPS (for the Windows agent)

The agent only talks HTTPS, and `runserver` is plain HTTP. Keep `runserver` for the browser (http://127.0.0.1:8000/) and run the API over HTTPS in a second window:

```powershell
cd D:\my_projects\windows-agent
.\monitoring-platform\.venv\Scripts\Activate.ps1
python -m pip install -r monitoring-platform\requirements-dev.txt   # only the first time
.\monitoring-platform\scripts\run_https.ps1
```

The script creates a local development certificate authority and a server certificate in `monitoring-platform\certs\` with `scripts\make_demo_cert.ps1`, when they are missing or the old certificate is not signed by that CA. The server certificate covers `localhost`, `127.0.0.1` and `::1`. It serves the API at `https://127.0.0.1:8443/api/agent/v1/`. Ctrl+C stops it and the whole process tree it started. The script refuses to start while anything already listens on port 8443.

**Trust the local CA once (Chrome and the Windows agent).** Run this in PowerShell as your own user (no administrator rights needed). It adds only the development CA to your Windows user store:

```powershell
certutil -user -addstore Root "D:\my_projects\windows-agent\monitoring-platform\certs\rootCA.pem"
```

Chrome reads the Windows store, so the dashboard at `https://127.0.0.1:8443/` then loads without a warning. Restart Chrome after adding it. Never put `rootCA-key.pem` anywhere else; it can sign certificates for your machine.

The agent does not use the Windows store. Its `ca_bundle` must point at `rootCA.pem`. Run the agent from the repository root, since the path is relative, and set in the agent config:

```toml
base_url = "https://127.0.0.1:8443/api/agent/v1"
ca_bundle = "monitoring-platform/certs/rootCA.pem"
```

An agent installed earlier with `ca_bundle` set to `cert.pem` must be updated to `rootCA.pem`, or reinstalled with `/CAFILE=<path to rootCA.pem>`. Verification stays on in both cases.

To register an agent, sign in as an Admin, open the employee's page, click "Create registration code", and put the code in the agent config under `[registration] code`. Then run `winagent register` from the repository root.

## Connect an employee's PC (Admin steps)

The Admin starts it in the web app, and the employee finishes it from their own dashboard. The agent runs on the employee's PC, so the last steps happen there.

1. **Set the monitoring rules.** Sidebar, Monitoring, Monitoring settings (`/monitoring/settings/`).
2. **Add the employee and start setup.** Add the employee on People (`/workforce/people/`). Open them and click "Start monitoring setup". The employee gets an email with a sign-in link. The email does not contain a code.
3. **The employee signs in.** Their dashboard shows "Connect this PC". They click "Get my connection code". The code appears once, with the `config.toml` block to copy. It works once and expires after 7 days. Asking again makes earlier unused codes stop working.
4. **The employee installs the agent on their PC.** They copy the agent folder there, save the block as `config.toml`, then run `winagent register --config config.toml` and `winagent run --config config.toml`.
5. **Check the result.** The card on the employee's dashboard shows "Monitoring is on" once the agent connects. The Admin's checklist on the employee's page turns to done step by step, and the Admin dashboard shows the agent as online.

The Admin can also create a code themselves ("Create registration code" on the employee's page), and the same settings are shown there.

The API address in the block comes from `AGENT_BASE_URL` (and `AGENT_CA_BUNDLE`). In development the defaults point at the local HTTPS server (`scripts\run_https.ps1`). Only a PC that can reach that address can connect, so real employee PCs need a deployed server with a real certificate and `AGENT_BASE_URL` set to it.

## Create the first Super Admin

```powershell
$env:DJANGO_DEBUG = "1"
.\.venv\Scripts\python.exe manage.py createsuperuser
.\.venv\Scripts\python.exe manage.py runserver
```

Open http://127.0.0.1:8000/admin/ and sign in with that email.

## Settings and secrets

All secrets come from environment variables:

| Variable | Meaning |
|---|---|
| `DJANGO_DEBUG` | `1` for development. Anything else means production mode. |
| `DJANGO_SECRET_KEY` | Required unless debug or tests. |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated hosts. Default `localhost,127.0.0.1`. |
| `DB_ENGINE` | `postgres` or empty for SQLite. |
| `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL` | SMTP settings for invitation and password-reset emails. Set `EMAIL_HOST` to send real mail (Gmail for local development: see `.env.example`). Without it, sending fails and the app shows that the email could not be sent (development shows a startup warning). Outside development, `EMAIL_HOST` and a real `DEFAULT_FROM_EMAIL` are required. |
| `.env` file | Optional. Put local values such as the Gmail app password in `monitoring-platform/.env` (copy `.env.example`). It is git-ignored. Real environment variables take precedence. |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | PostgreSQL connection. The development defaults match `docker-compose.yml`. |

Without `DJANGO_DEBUG=1` and without a secret key, the server refuses to start. In production mode, HTTPS redirect and secure cookies are on.

## Deploy on Render

Create a PostgreSQL database and a Python web service from the Git repository:

| Setting | Value |
|---|---|
| Root directory | `monitoring-platform` |
| Build command | `pip install -r requirements.txt && python manage.py collectstatic --noinput` |
| Pre-deploy command | Not available on the Free plan. Leave empty. |
| Start command | `sh -c "python manage.py migrate --noinput && python manage.py create_initial_superadmin && hypercorn config.asgi:application --bind 0.0.0.0:$PORT"` |
| Health check path | `/login/` |

On the Free plan the start command runs the migrations and the Super Admin step before the server starts. Both are safe to repeat: `migrate` only applies changes that have not run yet, and `create_initial_superadmin` does nothing once a Super Admin exists. After the first successful start, delete `INITIAL_SUPERADMIN_PASSWORD` from Render, so that a Super Admin deleted later is not recreated with a known password.

Environment variables for the web service:
- `DJANGO_DEBUG=0` and `DJANGO_SECRET_KEY` (a long random value).
- `DATABASE_URL`: the database's internal connection string (Render fills it in).
- `AGENT_BASE_URL=https://<service>.onrender.com/api/agent/v1` (the address the agent installer uses).
- `INITIAL_SUPERADMIN_EMAIL` and `INITIAL_SUPERADMIN_PASSWORD`: create the first Super Admin without a shell. The step runs only when no Super Admin exists, so it never makes a duplicate. Optional `INITIAL_SUPERADMIN_NAME`. After the first successful deploy, delete `INITIAL_SUPERADMIN_PASSWORD` from Render.
- `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `DEFAULT_FROM_EMAIL`.
- `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` for screenshots (private bucket).

Render sets `RENDER_EXTERNAL_HOSTNAME` and `RENDER_EXTERNAL_URL` itself; the settings use them for allowed hosts and trusted origins.

Scheduled jobs (Render cron jobs, same root directory and environment variables):
- `python manage.py build_summaries`, every 5 minutes (dashboard totals).
- `python manage.py cleanup_screenshots`, once a day (screenshot retention).

Before the first real use, check: static files load (CSS and charts), the WinAgent installer is built with the Render address (`WINAGENT_API_URL`, and no local `WINAGENT_CA_FILE`) and is served from `agent_dist`, and screenshots upload to S3.

## Rules for working here

The standing rules are in `../.claude/rules/` (tenant isolation, RBAC, audit, API contract, module boundaries, beginner-friendly style). Use the skills `django-app-module`, `tenant-scoped-endpoint`, and `tenant-isolation-tests` for new work.

## Screenshot storage in development (local disk)

For now, development keeps screenshot images on this computer instead of in S3: `monitoring-platform/screenshot_data/` (`SCREENSHOT_LOCAL_DIR` changes it). No S3 stand-in is needed. `python manage.py init_storage` creates the folder. The agent still uploads through a signed, expiring link locked to one file, size and SHA-256, but the Django server itself receives the file and sends it back (`/storage/upload/...` and `/storage/file/...`). That is a development-only exception to "Django never handles image bytes". `LocalStorage` refuses to start outside development, and production keeps using S3. Set `SCREENSHOT_STORAGE=screenshots.storage.S3Storage` to go back to the S3 stand-in.
