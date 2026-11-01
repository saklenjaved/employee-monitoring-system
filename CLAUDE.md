# Windows Desktop Monitoring Agent

Python native Windows agent for the Employee Monitoring & Workforce Productivity System.

- **Source of truth:** the Technical Architecture Document (TAD), "Windows Desktop Monitoring Agent" (working draft).
- **Backend:** Django REST Framework over HTTPS. The backend is an existing system and is not part of this folder.
- **Storage (backend side):** PostgreSQL for metadata, private Amazon S3 for screenshot images.

## Non-negotiables

- No keystroke logging, typed-text capture, webcam, microphone or continuous screen recording.
- HTTPS/TLS only for all agent-to-backend traffic.
- The server decides organization ownership. Never trust an `organization_id` sent by the agent.
- Never log tokens, screenshot contents or unnecessary personal data.

## Where things are

- `.claude/rules/`: standing rules. Read all of them before changing code.
- `.claude/skills/`: step-by-step guides for repeated tasks.
- `.claude/agents/`: reviewer helper for checking changes against the rules.

## Status

This is a POC working draft. Items marked as open in TAD section 13 (Windows versions, installer approach, browser support, screenshot retention and blur rules, offline buffering, code signing) are not approved. Do not implement behavior that depends on them without asking first.
