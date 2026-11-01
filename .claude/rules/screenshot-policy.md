# Rule: Screenshot capture policy

Screenshots are the most sensitive data the agent handles. Capture only when all policy checks pass (TAD 6, 9).

Before every capture, the agent must check:
1. Screenshots are enabled for the organization, employee and device.
2. The current time is inside the configured monitoring hours.
3. The employee or device is not on an exclusion list.
4. The screen is not locked and monitoring is not paused.

Before upload:
- Apply the configured exclusions and blur or sensitive-content rules (TAD 9). The exact blur rules are open (TAD 13). Until approved, do not upload unblurred images from areas marked for exclusion.
- Keep images only as long as needed for upload. Delete the local copy after a successful upload. The local retention period for failed uploads is open (TAD 13), so do not keep failed images beyond the limit in the approved configuration.
- Request an upload link from Django first, then upload directly to S3 using that link. Images must not pass through the Django application server (TAD 9).
- Report upload completion so the backend can update metadata and write the audit log.

Never:
- Store screenshots in the database as blobs (TAD 4).
- Keep screenshots in a shared or readable folder.
- Capture webcam, microphone or continuous recordings (TAD 1.2).

**Why:** Screenshots can show private messages and personal data. Policy checks and deletion limit the exposure.

**How to apply:** Use the `screenshot-flow` skill for implementation. Review any capture change with the `agent-reviewer` agent. Related: [[privacy-no-keystrokes]], [[sync-and-idempotency]].
