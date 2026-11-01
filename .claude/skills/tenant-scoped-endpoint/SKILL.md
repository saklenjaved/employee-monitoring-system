---
name: tenant-scoped-endpoint
description: Add or change a backend API or view safely, with organization scoping, role checks, audit and contract updates. Use for any new or changed endpoint in the Django backend.
---

# Tenant-scoped endpoint

Follow these steps in order. Do not skip any.

1. **Find the organization on the server.** Take it from the logged-in user or the registered device. Never read `organization_id` from the request body or URL.
2. **Check the role.** Apply [[rbac-permissions]]: Admin, Manager (assigned teams only) or Employee (own data only).
3. **Scope the query.** Filter by organization, then by role scope. Look up records by ID and organization together. See [[tenant-isolation]].
4. **Validate input.** Use a serializer or form. Reject unknown or unexpected fields.
5. **Do the work in a service function.** Keep the view short. See [[django-module-boundaries]].
6. **Audit if sensitive.** Call the audit service for sensitive actions. See [[audit-sensitive-actions]].
7. **Update the contract.** Change `docs/api-contract.yaml` in the same change. See [[api-contract-sync]].
8. **Write tests.** Cover the allowed case, a denied role, and another organization's record. Use the `tenant-isolation-tests` skill.

## Checks before finishing
- No query is missing an organization filter.
- Denied requests return "forbidden" or "not found" with no data.
- The contract and tests are updated.
- Have the `backend-permission-reviewer` agent check the change.
