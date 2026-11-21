# Changelog (server)

## 2026-11-02: Dynamic dashboards and demo data

- Fixed 15 test errors from a removed `User.profile` relation. Person and screenshot lookups now use the organization's `EmployeeProfile`.
- Added `python manage.py seed_demo`: idempotent demo organizations, users of each role, devices and 30 days of activity. Refuses to run unless `DJANGO_DEBUG=1` or `DEMO_SEED_ALLOWED=1`.
- Added `SQLITE_PATH` so local demo data can live in its own database file.
- Admin dashboard: live agent and activity panel refreshed every 10 seconds (HTMX).
- Trend charts (Chart.js) on organization, team, employee and self dashboards, with a table view. Data comes from `trend.json` endpoints that apply the same role and organization checks as their pages.
- List pages (people, screenshots) search as you type and swap only the page body.
- Toast messages with auto-close, and a loading bar.
- Tests: 533 passing (`python manage.py test`).
