---
name: dashboard-view
description: Add a dashboard page or dashboard API (employee, team, organization, or Super Admin) with role limits, organization scoping and charts. Use for any new dashboard.
---

# Dashboard view

Use this for each dashboard in PRD FR-11 to FR-14.

1. **Pick the audience.** Employee, Manager (team), Admin (organization) or Super Admin (platform). Each has different data limits.
2. **Scope the data.** Apply [[tenant-isolation]] and [[rbac-permissions]] before any totals are calculated.
3. **Use summaries.** Read from aggregated summaries, not raw events, for dashboard totals (TAD 12).
4. **Use filters.** Support date range and the filters from PRD FR-15. Keep the filter names the same across dashboards.
5. **Build the view.** Use Django Templates for the page and DRF for any data endpoint (TAD 2). Keep logic in a service function.
6. **Charts.** Use the `dataviz` skill so the charts look consistent.
7. **Test.** Check the data for each role, and that a Manager cannot see an unassigned team.

## Checks before finishing
- Each role sees only its own scope.
- Totals match the raw data for a small test set.
- Large date ranges use summaries and pagination.
- The contract is updated if an API was added (see [[api-contract-sync]]).
