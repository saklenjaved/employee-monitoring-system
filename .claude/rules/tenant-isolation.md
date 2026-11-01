# Rule: Tenant (organization) isolation

Each customer organization must only see its own data. This applies to the Django backend (TAD 5).

- Every organization-owned record has an `organization_id`.
- The server finds the organization from the logged-in user or the registered device. Never from a value sent by the client.
- Every query, create, update, delete, dashboard, export and screenshot access must filter by the organization.
- Do not look up a record by ID alone. Look it up by ID and organization together, so another organization's record returns "not found".
- Managers and Employees are limited further by [[rbac-permissions]].
- Every new feature needs a test that proves another organization cannot access its data. See the `tenant-isolation-tests` skill.

**Why:** Mixing organizations' data is a serious breach and breaks the PRD promise of isolated data.

**How to apply:** Use the `tenant-scoped-endpoint` skill for new endpoints. Have the `backend-permission-reviewer` agent check changes. Related: [[rbac-permissions]], [[audit-sensitive-actions]].
