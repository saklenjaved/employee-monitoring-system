---
name: tenant-isolation-tests
description: Write tests that prove one organization cannot see or change another organization's data, and that each role only sees its scope. Use for every new backend feature and before merging backend changes.
---

# Tenant isolation tests

These tests protect the core promise in PRD section 13. Follow [[tenant-isolation]] and [[rbac-permissions]].

1. **Create two organizations.** Each has its own admin, manager, employee, team and records.
2. **Test cross-organization access.** Log in as organization A's admin and try to read, change and delete organization B's record. Each attempt must return "not found" or "forbidden", and the record must be unchanged.
3. **Test role limits inside one organization.**
   - A Manager can see an assigned team but not an unassigned team.
   - An Employee can see their own data only.
   - An Admin can see the whole organization but not another organization.
4. **Test lists and exports.** Check that a list, search, dashboard and CSV export never include another organization's rows.
5. **Test screenshot access.** Confirm that viewing or downloading another organization's screenshot is denied and no audit entry is written for the denied access as if it succeeded.
6. **Keep tests small.** One test for each rule. Use simple fixture helpers.

## Checks before finishing
- Every new endpoint has a cross-organization test.
- Denied cases check that no data is returned.
- Tests run with the rest of the backend test suite.
