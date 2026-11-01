# Rule: Role-based access control (RBAC)

The backend must enforce the four roles from PRD section 2 and TAD section 4.

- **Super Admin:** manages organizations and platform settings.
- **Admin:** manages their own organization only.
- **Manager:** sees only assigned teams.
- **Employee:** sees only their own information.

Requirements:
- Check the role and scope on the backend for every protected view, API, export and screenshot action.
- Hiding links or buttons in templates is not access control.
- Denied access returns "forbidden" or "not found". It must never return the data.
- Screenshot viewing and download need explicit permission checks (see [[audit-sensitive-actions]]).
- Permission changes are themselves audited.

**Why:** The PRD makes role limits a core requirement. A missing check exposes employee data to the wrong people.

**How to apply:** Put the check in one shared permission class or service, not repeated in each view. Use the `tenant-scoped-endpoint` skill. Related: [[tenant-isolation]].
