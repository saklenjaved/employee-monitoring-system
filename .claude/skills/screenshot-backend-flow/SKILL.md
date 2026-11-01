---
name: screenshot-backend-flow
description: Implement or change the backend side of screenshots (upload authorization, metadata, upload completion, viewing and download with audit). Use for any Django work on screenshots. The agent side is covered by screenshot-flow.
---

# Screenshot backend flow

This is the server half of the flow in TAD section 7. The agent half is in the `screenshot-flow` skill.

1. **Authorize upload.** The endpoint checks the device credential, the organization screenshot toggle, monitoring hours, and employee and team exclusions. If any check fails, return a clear refusal and create no record.
2. **Create metadata.** Store the screenshot record with organization, employee, device, timestamp and status. Store only a storage reference, never the image bytes (TAD 7).
3. **Issue the upload link.** Return a short-lived, object-specific pre-signed URL. Do not cache or reuse it.
4. **Complete upload.** The completion endpoint marks the record as uploaded and checks that the object exists. Record failures as failures.
5. **View and download.** Check [[rbac-permissions]] (Admin, or Manager with download permission for their team). Use a short-lived link. Apply [[tenant-isolation]].
6. **Audit every view and download.** See [[audit-sensitive-actions]].
7. **Keep it async where needed.** Cleanup and retention run as management commands (TAD 9).

## Checks before finishing
- No image passes through the Django server.
- Every view and download writes an audit entry.
- Signed URLs and tokens are never logged.
- The contract is updated in `docs/api-contract.yaml` (see [[api-contract-sync]]).
- Run the `tenant-isolation-tests` skill, then have `backend-permission-reviewer` and `privacy-reviewer` check the change.
