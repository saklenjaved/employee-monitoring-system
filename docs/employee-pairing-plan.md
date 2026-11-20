# Employee pairing and silent install plan

Status: approved workflow. Implementation not started. Decisions marked "default" can be changed.

## Workflow (no terminal, no codes typed by the employee)

1. The Admin clicks **Start Monitoring Setup** for an employee. The server opens a setup window for that
   employee for 30 minutes (default) and emails a sign-in link. No code is in the email.
2. The agent is installed silently on the PC (see below). It starts in the background at sign-in. It has no
   credential, so it creates a **pairing request** (PC name, OS, agent version; no employee identity) and
   shows "Waiting for approval" in the tray.
3. While a setup window is open for an employee, that employee's dashboard shows **"Approve this PC?"** with
   the PC name and an **Approve** button.
4. The employee re-enters their **account password** and clicks **Approve** (decided: required).
5. The server creates the device under the employee's active membership, using the same checks as the
   registration code path. The agent polls with its private token, receives its credential once, and stores
   it with DPAPI. The pairing request is then closed.
6. The dashboard shows Connected, and the employee's PIN start works as before.

### Rules

- A pairing request is useful only to the employee who approves it. Approval runs in their logged-in session
  and needs their password, so a stray PC cannot receive credentials.
- Pairing requests expire after 10 minutes (default). One open request per PC name.
- A pairing request never reveals an employee to the agent. The credential is returned only once.
- The Admin's click is the only way to open a setup window. No window means no approval prompt.
- Audit: setup started, pairing requested, pairing approved (with the PC name), pairing expired, device revoked.
- Privacy and security rules are unchanged: HTTPS only, no secret in the installer, no organization id from the
  agent, no token or password in logs.

## Silent installation (recommended approach)

**Chosen: a per-user Inno Setup installer (one `.exe`), with silent switches.**

- Runs without admin rights (`PrivilegesRequired=lowest`), so no UAC prompt.
- Silent run: `WinAgentSetup.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-`
- Installs to the user's profile, registers the Run key (the same as `winagent autostart enable`), and starts
  the agent hidden with `runhidden` and no `skipifsilent`.
- Contains no secret and no employee data. Pairing happens in the browser, so one installer serves everyone.
- Uninstall is available in Settings > Apps, and the Run key is removed with it.

### Deployment options

| Option | How | Who runs it | Notes |
|---|---|---|---|
| 1-click download link | Dashboard or email link to `WinAgentSetup.exe`, run with the silent switches from the employee's own download | Employee, once | Needs code signing to avoid SmartScreen warnings |
| Admin or IT script | `WinAgentSetup.exe /VERYSILENT ...` from a logon script, RMM tool or Intune | IT | No employee action |
| Group Policy | Windows Installer `.msi` assigned per user or per computer | IT | GPO software install needs an MSI. A per-user MSI is planned if GPO is required |

Recommended first: the one-click link for employees, and an IT script for managed PCs. An MSI can be added later
if the company requires GPO.

## Open items (need a decision)

1. **Code signing certificate.** Without it, Windows shows warnings on the download, and silent install is
   less reliable. This needs a purchase or an internal certificate.
2. **Installer tool.** Inno Setup (recommended, single file) or WiX (MSI, needed for GPO).
3. **Auto-update.** Not in scope. Each new version needs a new install.
4. **Setup window length.** 30 minutes default.
5. **Pairing request expiry.** 10 minutes default.

## Build order

1. Server: setup window, pairing requests, employee Approve page with password, audit, tests.
2. Agent: pairing request and poll, credential storage, tray state, no connect window.
3. Installer: Inno Setup script, silent test on a clean Windows 11 user account, uninstall test.
4. Deployment: download page, IT script example, and the TAD and `open-decisions.md` update.
