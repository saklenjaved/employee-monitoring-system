# Rule: Project scope and architecture

This rule keeps the agent inside the scope approved in the TAD.

**In scope (TAD 1.1):**
- Device registration and connection status.
- Active application and focused-window information.
- Supported website activity, using the browser method approved for v1.
- Active and idle time from input-idle signals.
- Policy-controlled screenshots.
- Sync of events and screenshot metadata to the backend.
- Showing the employee that the agent is installed and its status.

**Out of scope (TAD 1.2):**
- Continuous screen recording, webcam and microphone.
- Keystroke logging and typed-text capture.
- Payroll or salary calculation.
- Domain blocking and advanced USB/device monitoring.
- AI predictions or recommendations.

**Architecture boundaries:**
- The agent is Python and runs on the employee's Windows device.
- The backend (Django REST Framework, PostgreSQL, private S3) is owned by the existing platform. This project does not change its design.
- Business rules such as org policy, role access and audit logging are enforced by the backend. The agent applies the policy it receives.

**Change control:** Any feature outside the scope above, or any change that depends on an open TAD section 13 item, needs explicit approval before implementation. Do not add a feature because it seems useful.

**Why:** Monitoring tools can grow into surveillance quickly. A fixed scope protects employees and keeps the POC reviewable.

**How to apply:** Before starting a task, confirm it is in scope and not blocked by an open decision. Related: [[privacy-no-keystrokes]], [[security-device-auth]].
