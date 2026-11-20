# Rule: Audit sensitive actions

Important actions must leave a record in the audit log (PRD FR-21, TAD 3 `audit`).

Always audit:
- Screenshot viewing and downloading.
- Monitoring policy and screenshot policy changes.
- Permission and role changes.
- Classification rule changes.
- Organization create, update and deactivate actions.
- Employee activation and deactivation.
- A user changing their own full name (`user.name_changed`). The approved exception below applies.

Each audit entry records: who acted, what action, which organization and which employee or record, and when.

Rules:
- Audit entries are append-only. No normal user can edit or delete them.
- Never write tokens, signed URLs, screenshot contents or personal details beyond the record ID into the audit log.
- Approved exception: the `user.name_changed` entry holds the old and new full name (each cut to 120 characters). It never holds an email address. Nothing else may copy this exception.
- Audit writes happen in the same database transaction as the action they record.

**Why:** Admins and auditors must be able to see who looked at sensitive data and who changed monitoring rules.

**How to apply:** Use the audit service in the `audit` app, not ad hoc logging. Related: [[tenant-isolation]], [[rbac-permissions]].
