---
name: add-agent-monitor
description: Add a new collector or monitor to the Windows agent (application, website or activity signal) in the standard component structure from TAD section 5.1.
---

# Add an agent monitor

Use this when adding a new kind of signal to the agent. The TAD defines application, website and activity monitors (TAD 5.1).

1. **Confirm scope.** Check the signal is in scope (see [[project-scope-and-architecture]]). Keystrokes, typed text, webcam and microphone are never allowed (see [[privacy-no-keystrokes]]).
2. **Check open decisions.** Browser support and idle thresholds are open in TAD 13. If the monitor depends on an open item, stop and ask.
3. **Create the module.** Put it in its own module with one class. Keep Windows calls behind an interface so tests can use a fake. Catch specific exceptions and never let one failing monitor stop the agent.
4. **Define the event.** Each event has a unique event ID, the device ID, a timestamp, the signal type and its values. Include no content that could reveal typed text or private page content.
5. **Hand off to sync.** Pass events to the sync manager. Do not send directly from the monitor (see [[sync-and-idempotency]]).
6. **Handle failure.** Skip a failing sample and log it without sensitive content (see the `security-device-auth` rule for what may not be logged).
7. **Test it.** Use the `testing-and-debugging` skill. Cover normal input, permission-denied failures and duplicate prevention. Use fakes for Windows calls.
8. **Document it.** Note the new event type in the team's API notes so the backend can accept it.
