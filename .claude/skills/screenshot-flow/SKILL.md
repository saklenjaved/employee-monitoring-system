---
name: screenshot-flow
description: Implement or change the screenshot capture and upload path (policy check, capture, upload link, direct S3 upload, completion). Use for any work on screenshots.
---

# Screenshot flow

Follow the order in TAD section 9. Do not skip or reorder steps.

1. **Policy check (agent).** Confirm screenshots are enabled for the organization, employee and device, and that the current time is inside monitoring hours. Check exclusions. Stop if any check fails. See [[screenshot-policy]].
2. **Capture.** Capture with `mss` or Pillow (TAD 5.2). Do not capture a locked screen.
3. **Exclusions and blur.** Apply the configured exclusions and blur rules before anything is saved to disk. If the blur rules are still open (TAD 13), stop and ask.
4. **Upload authorization.** Call the upload-authorization endpoint with the device credential. Django re-checks policy and returns a short-lived, object-specific pre-signed S3 URL.
5. **Direct upload.** Upload the file straight to S3 with that URL. Do not send image bytes through the Django app server.
6. **Completion.** Call the upload-completion endpoint so the backend updates metadata and writes the audit log.
7. **Cleanup.** Delete the local file after a successful upload. For failed uploads, keep it only within the approved retention limit.

## Checks before finishing
- No image is written to a shared or readable location.
- URLs and tokens are not logged.
- A failed upload does not block event sync (see [[sync-and-idempotency]]).
- Use the `testing-and-debugging` skill to verify the change, and have the `agent-reviewer` agent check it.
