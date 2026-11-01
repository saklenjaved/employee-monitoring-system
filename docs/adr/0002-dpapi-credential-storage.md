# ADR 0002: Device credential in DPAPI, current-user scope

Status: accepted.

- The credential is encrypted with `CryptProtectData` without the LOCAL_MACHINE flag. Only the same Windows user can decrypt it. No admin rights are needed.
- No plaintext fallback. If DPAPI fails, storing fails.
- The token is never logged. `DeviceCredential.__repr__` hides it.
- Rule: `.claude/rules/security-device-auth.md`.
