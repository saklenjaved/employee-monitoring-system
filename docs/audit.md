# Audit: Employee Monitoring platform (backend/) and Windows agent

Date: 2026-11-02. Scope: the repository at `D:\my_projects\windows-agent`, as found before this session's changes.

## What is in the repo

| Part | Location | State |
|---|---|---|
| Windows agent (POC) | `winagent/`, `tests/`, `scripts/`, `build.ps1`, `winagent.spec` | Complete POC. Already at the project root. No separate folder to merge. |
| Django platform | `backend/` | Most of Phases 1 to 6 of `docs/roadmap.md` present: accounts, organizations, access control, workforce, devices, monitoring, productivity, analytics, screenshots, audit. |
| Platform TAD | `docs/TAD.md` (was the agent TAD) | Replaced this session with the platform TAD converted from the attached docx. The agent TAD is kept as `docs/TAD-windows-agent.md`. |
| Agent plan | `docs/agent-implementation-plan.md` (was `implementation-plan.md`) | Renamed to avoid clashing with the platform plan. |
| Rules and skills | `.claude/rules/`, `.claude/skills/`, `.claude/agents/` | Present and consistent with `CLAUDE.md`. |

## Baseline (before changes)

- `manage.py test`: 516 tests, **15 errors**. All were stale references to a `User.profile` relation that no longer exists. The records now live in `EmployeeProfile`, looked up per organization. Affected: `screenshots/views.py`, `workforce/views.py`, two templates, and two tests.
- `manage.py check`: fails without `DJANGO_SECRET_KEY` outside debug mode. This is intended. With `DJANGO_DEBUG=1` it reports no issues.
- `backend/db.sqlite3` holds real local data (1 organization, 3 users, no events or devices). It was not migrated or seeded.

## Fixed in this session

1. Replaced the stale `profile` lookups with per-organization `EmployeeProfile` lookups (people list, person detail, screenshot list and filters). Result: 516 tests pass.
2. Added the `seed_demo` command (idempotent). It creates two organizations, a Super Admin, Admins, Managers and Employees, devices with heartbeats, classification rules, and 30 days of daily activity in a separate database (`demo.sqlite3`, selected with `SQLITE_PATH`).
3. Added the dynamic UI:
   - Admin dashboard: live panel polled every 10 seconds with HTMX (`dashboard/organization/live/`).
   - Trend charts (Chart.js) on the organization, team, employee and self dashboards, fed by JSON endpoints. Each has a table view.
   - People, screenshot and other list pages: search while typing, filters, sort and paging swap only the page body.
   - Toast messages, loading bar, CSRF header for HTMX.
4. Tests: 17 new tests for trend values, tenant isolation, role limits, the live panel, HTMX attributes and `seed_demo`. Total: 533, all passing.

## Gaps found (not fixed here)

- **TAD phases not built:** attendance and breaks, reports and CSV exports, notifications, integrations, productivity goals, Chrome extension. These are Phase 8 and 9 items in `docs/roadmap.md`.
- **Phase 2 device registration by the agent** and the agent API contract (`docs/api-contract.yaml`) are not re-verified against the code in this audit.
- **Layout at phone width** was not checked on screen. The CSS has a phone breakpoint, but the new charts and wide tables have not been viewed at 375px.
- **Chart library and HTMX are loaded from cdnjs** without a local copy, so the dashboards need internet access.
- **Git state:** the working tree already had uncommitted changes before this session (`.claude/rules/audit-sensitive-actions.md`, `.gitignore`, `CLAUDE.md`, `docs/api-contract.yaml`, `docs/event-schema.json`, and an untracked `backend/`). None were committed or staged in this session.
