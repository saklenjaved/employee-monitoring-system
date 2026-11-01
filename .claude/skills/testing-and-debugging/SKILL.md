---
name: testing-and-debugging
description: General approach for testing, reproducing and debugging problems in the Windows agent, including checks for privacy, sync and screenshot behavior, without collecting sensitive data. Use when writing tests or when something fails or behaves unexpectedly.
---

# Testing and debugging

## Testing
1. **Unit tests first.** Test logic without Windows. Replace the foreground-window reader, idle-time reader, screen capture and HTTP client with fakes.
2. **Windows checks.** Run a small set on a real Windows machine for foreground window, idle time and capture. Mark these so they run separately from unit tests.
3. **Network recovery.** Use a fake server that drops connections, returns errors and accepts duplicates. Check retries, backoff and no duplicates (see [[sync-and-idempotency]]).
4. **Policy.** Test monitoring hours, exclusions and screenshot policy, including turning policy off mid-session (see [[screenshot-policy]]).
5. **Privacy checks.** Assert that no keyboard hook is used, no typed text appears in events and no secrets appear in logs (see [[privacy-no-keystrokes]] and [[security-device-auth]]).
6. **Report.** State what passed, what was skipped and why. Do not call an area verified if it was only checked with fakes.

## Debugging
1. **Reproduce.** Write down the exact steps, Windows version, agent version and whether the device was online. If you cannot reproduce it, say so.
2. **Use health data first.** Look at agent version, last successful sync, error type and queue count. Do not ask for screenshots or event content to debug.
3. **Isolate.** Decide whether the problem is in the monitor, the sync logic, the network or the backend. Use fakes to test one part at a time.
4. **Check the rules.** Many bugs come from breaking a rule, such as wrong idle logic, a duplicate event or a token in a log.
5. **Fix the cause.** Add a test that fails before the fix and passes after it.
6. **Verify.** Run the affected tests and the privacy checks. Report results honestly, including failures.

## Safe debugging
- Use test devices and test accounts only.
- Do not turn on extra capture (screenshots or input) to debug. Add logs that contain no content.
- Remove debug output before sharing code. Never commit tokens, screenshots or real employee data as test fixtures.

## When to stop and ask
- The fix needs a change to an open TAD 13 item.
- The bug suggests data was collected outside policy. Report it right away and stop testing with real data.
