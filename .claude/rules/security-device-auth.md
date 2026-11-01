# Rule: Device authentication and server-side trust

- All agent-to-backend traffic uses HTTPS/TLS. No plain HTTP fallback, and never disable certificate verification.
- The device credential is issued at registration and must be stored encrypted at rest on the Windows device (TAD 11). Never store it in plain text, in source code, in a config file in the repo, or in logs.
- Every request identifies the device. The backend validates the device credential, the employee and the organization on each call.
- The backend resolves organization and employee ownership from the device record. The agent must never be trusted to claim `organization_id` or `employee_id` as proof of access (TAD 11).
- Screenshot upload links are short-lived and object-specific (TAD 9, 11). Do not cache them beyond their use.
- On expired or invalid credentials, stop sync and request re-registration. Do not retry forever.
- Never log tokens, signed URLs or credential values (TAD 11).

**Why:** A stolen or spoofed device token would let someone send fake data or read screenshots. Server-side checks limit the damage.

**How to apply:** Any new endpoint or client call must follow these checks. Use the `agent-reviewer` agent to check changes. Related: [[screenshot-policy]], [[project-scope-and-architecture]].
