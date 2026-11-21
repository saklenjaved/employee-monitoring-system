# Implementation Roadmap

Employee Monitoring & Workforce Productivity System.
References: PRD v5.0 and the Technical Architecture Document (TAD). The PRD and TAD are the primary sources.

The Windows Monitoring Agent POC is a completed component and is not rebuilt. This roadmap covers the Django platform and its integration with the agent.

## 1. Decisions

### Confirmed by the product owner
| Topic | Decision |
|---|---|
| Backend location | Inside this repository (`windows-agent/`), in its own `monitoring-platform/` folder. The agent code is not changed. |
| Activity data | Idle time only (time since last input). No keyboard or mouse interaction counts. |
| Website tracking | Backend accepts website events now. The Chrome extension is a later, separate phase. |
| Screenshot access | Admin and assigned Managers can view. Download needs explicit permission. Employees cannot view screenshots. Every view and download is audited. |
| Screenshot settings | Frequency, interval, retention and blur are Admin-configurable per organization. No default: screenshots stay disabled until the Admin chooses these values. |
| Devices | One employee can have several devices. Admin generates single-use registration codes in the web UI. |
| P1 in the first release (after P0) | Usage trends, search and filters, CSV export, audit log screen, notifications, software utilization and license ROI. |
| Integrations | Deferred. Late phase, no providers chosen. |
| Login and hosting | Email and password with password reset by email. No MFA in the first release. Local Docker for development, AWS EC2 later. |

### Defaults used where no preference was given (to confirm)
| Topic | Default | Status |
|---|---|---|
| Employee web login | Employees can log in and use the self-view dashboard. | default, to confirm |
| Super Admin data access | Super Admin manages organizations and platform data only. Employee timelines and screenshots are not visible. | default, to confirm |
| Platform support | Windows only for the first release (TAD). macOS and Linux are later. | default, to confirm |
| P0 scope | The full P0 list in PRD section 10. | default, to confirm |
| Attendance, breaks, goals | After the P1 items above. The timeline shows active, idle and offline only until then. | default, to confirm |

### Still open
- Exact retention periods for events and summaries.
- Monitoring notice text shown to employees.
- Email provider for password reset and notifications.
- AWS region, managed PostgreSQL or self-hosted, backup schedule.
- License-cost data source for ROI comparison.
- Registration-code expiry time.

## 2. Existing Windows agent: integration work

The agent is finished. The real backend must match `docs/api-contract.yaml` and `docs/event-schema.json`:
- Endpoints: register, config, events batch, screenshot authorize, screenshot complete, heartbeat.
- Events carry a unique `event_id`. The server ignores duplicates.
- The server finds organization and employee from the device. It never trusts client-sent IDs.

Agent-side items to do when the real backend is ready (not now):
- Point `config.toml` at the real URL and certificate.
- Revert the temporary test values: screenshot interval 15 s back to 600 s, code minimum 15 s back to 60 s, policy refresh 60 s back to 300 s.
- Update the agent `CLAUDE.md`, which says the backend is outside this folder.

## 3. Phases

Each phase ends with something that can be run and checked. Tenant-isolation and role tests are part of every phase.

### Phase 0: Setup
- **Develop:** Django project in `monitoring-platform/`, PostgreSQL in Docker, local S3 stand-in (MinIO), test setup, settings with secrets outside source code.
- **Apps:** project settings only.
- **Depends on:** nothing.
- **Outcome:** server starts, database connects, empty test suite runs.
- **Verify:** `manage.py check`, migrate on an empty database, test command passes.

### Phase 1: Accounts, organizations, access control (P0)
- **Develop:** email and password login, password reset by email, organizations, Super Admin and Admin accounts, four roles, permission checks, basic audit log.
- **Apps:** `accounts`, `organizations`, `access_control`, `audit`.
- **Depends on:** Phase 0.
- **Outcome:** Super Admin creates an organization and an Admin. Users see only their own organization.
- **Verify:** two-organization isolation tests, login and reset tests, audit entries for admin actions.

### Phase 2: Workforce and devices (P0)
- **Develop:** departments, teams, employees, manager-team assignment, registration codes, device registration, device credentials, agent config and heartbeat endpoints.
- **Apps:** `workforce`, `devices`, `access_control`.
- **Depends on:** Phase 1.
- **Outcome:** an Admin creates an employee and a code. The real agent registers and receives its policy.
- **Verify:** run `winagent register` and `run` against the backend and check status shows connected. Tests for single-use and expired codes, invalid credentials, and an agent claiming another organization.

### Phase 3: Monitoring ingestion (P0)
- **Develop:** batch event endpoint, validation against `event-schema.json`, duplicate handling by `event_id`, storage with organization and employee.
- **Apps:** `monitoring`, `devices`.
- **Depends on:** Phase 2.
- **Outcome:** agent events arrive and are stored exactly once.
- **Verify:** same batch sent twice stores one copy. Offline replay from the agent causes no duplicates. Unknown fields are rejected.

### Phase 4: Classification and summaries (P0)
- **Develop:** productive, unproductive, neutral categories, global, team and role rules, unclassified list, usage summaries without double-counting, active and idle totals, activity percentage.
- **Apps:** `productivity`, `analytics`, `monitoring`.
- **Depends on:** Phase 3.
- **Outcome:** totals come from summaries, with each event classified once.
- **Verify:** small data set with known totals, overlapping events counted once, rule changes audited.

### Phase 5: Dashboards (P0)
- **Develop:** Employee, Manager (team), Admin (organization) and Super Admin (platform) dashboards, with the nine usage signals.
- **Apps:** `analytics`, `workforce`, `organizations`.
- **Depends on:** Phase 4.
- **Outcome:** each role sees its own dashboard.
- **Verify:** role tests per dashboard, Manager cannot see unassigned teams, totals match test data.

### Phase 6: Screenshots (P0)
- **Develop:** server-side policy checks (organization toggle, monitoring hours, exclusions), upload authorization, direct upload to storage, completion, management dashboard, permission-checked view and download with audit, retention cleanup command.
- **Apps:** `screenshots`, `audit`, `devices`.
- **Depends on:** Phases 1, 2, 3. Admin must have set blur, frequency and retention.
- **Outcome:** screenshots exist only when policy allows, and every access is recorded.
- **Verify:** disabled organization gives no upload link, excluded employee gets no link, no image bytes pass through Django, audit entry for each view and download, cross-organization access denied.

### Phase 7: Timeline and employee self-view (P0)
- **Develop:** workday timeline (active, idle, offline), apps and websites per period, screenshots at their timestamps, employee dashboard widgets.
- **Apps:** `analytics`, `monitoring`, `screenshots`, `workforce`.
- **Depends on:** Phases 4, 5, 6.
- **Outcome:** a Manager opens an employee's day. An employee sees their own day.
- **Verify:** timeline matches raw events, employee cannot open another employee's timeline, date filter shows only that day.

### Phase 8: P1 items chosen for the first release
- **Develop:** usage trends, search and filters, CSV export (permission-aware), audit log screen, notifications (agent offline, screenshot failure, sync failure), software utilization and license ROI.
- **Apps:** `analytics`, `reports`, `notifications`, `audit`.
- **Depends on:** Phases 4 to 7. License ROI also needs the license-cost data source decision.
- **Outcome:** reports and alerts work with role limits.
- **Verify:** exports contain only permitted rows, notifications reach only authorized users, tenant tests for every export.

### Phase 9: Later P1 items
- **Develop:** attendance, breaks and productivity goals; scheduled daily, weekly and monthly reports; Chrome extension for website tracking; integrations (providers to be chosen).
- **Apps:** `attendance`, `reports`, `integrations`, `analytics`.
- **Depends on:** Phase 8 and the open decisions above.
- **Verify:** same approach: role tests, tenant tests, audit entries.

### Phase 10: Late P2
- Advanced analytics and alerts first.

### Phase 11: Hardening and deployment
- **Develop:** security review, privacy review, load check, EC2 deployment with HTTPS, private S3 bucket, backups and a tested restore.
- **Verify:** `security-review`, `privacy-reviewer` and `backend-permission-reviewer` report no blockers. A restore from backup works on a clean server.

## 4. Recommended order and reasons

1. Phases 0 to 2 first. Organizations, employees and devices define who owns every record. Changing them later would force rewrites.
2. Phase 3 next. The agent is ready, so ingestion proves the integration early.
3. Phases 4 and 5 use the stored events.
4. Phase 6 waits until permissions and audit are proven, because screenshots are the most sensitive data.
5. Phase 7 needs events, summaries and screenshots.
6. P1 and P2 follow only when P0 is stable.

## 5. First task

Phase 0 and the start of Phase 1: create the Django project skeleton, then the `organizations` and `accounts` apps with the first tenant-isolation test. This task is small, and every later phase depends on it.

## 6. Approval needed before starting

- Approve this roadmap or send changes.
- Confirm the "default, to confirm" rows.
- Answer the "Still open" items when their phase approaches (retention, email provider, hosting, license-cost data).
