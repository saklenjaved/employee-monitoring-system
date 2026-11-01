# Rule: No keystroke logging or typed-text capture

The agent must never record what the employee types. It may only measure *whether* input happened and *when*.

- Allowed: time since last input using Windows `GetLastInputInfo`, which returns a timestamp only (TAD 5.2).
- Forbidden: keyboard hooks (`SetWindowsHookEx` with keyboard hooks, `pynput` keyboard listeners, raw input capture of key codes), clipboard reads, IME/text capture, and any event field that contains typed content.
- Forbidden: webcam, microphone and continuous screen recording (TAD 1.2, 11).

**Why:** Actual keystroke logging is explicitly out of scope and is a serious privacy violation for employees. The idle-time signal gives the needed active/idle result without it.

**How to apply:** If a task seems to need key content, stop and flag it. Do not implement it. Check every new event field against this rule. Related: [[project-scope-and-architecture]], [[security-device-auth]].
