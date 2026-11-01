# ADR 0009: RDP, Citrix and VDI sessions are not monitored

Status: accepted (POC default).

- Session type is detected with `GetSystemMetrics(SM_REMOTESESSION)`, and Citrix via a process check.
- In any non-console session, the agent logs the session type only, collects nothing, and reports `unsupported_session` in the heartbeat.
- Screenshots also skip non-console sessions (`policy.skip_reason`).
