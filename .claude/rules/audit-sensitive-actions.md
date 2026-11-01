# Rule: Audit sensitive actions

Important actions must leave a record in the audit log (PRD FR-21, TAD 3 `audit`).

Always audit:
- Screenshot viewing and downloading.
- Monitoring policy and screenshot policy changes.
- Permission and role changes.
- Classification rule changes.
- Organization create, update and deactivate actions.
- Employee activation and deactivation.

Each audit entry records: who acted, what action, which organization and which employee or record, and when.

Rules:
- Audit entries are append-only. No normal user can edit or delete them.
- Never write tokens, signed URLs, screenshot contents or personal details beyond the record ID into the audit log.
- Audit writes happen in the same database transaction as the action they record.

**Why:** Admins and auditors must be able to see who looked at sensitive data and who changed monitoring rules.

**How to apply:** Use the audit service in the `audit` app, not ad hoc logging. Related: [[tenant-isolation]], [[rbac-permissions]].
