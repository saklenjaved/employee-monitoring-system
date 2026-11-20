# WinAgent: IT deployment (silent, no employee action)

Status: installer script written (`installer/winagent.iss`). Not compiled or tested yet. Unsigned until code
signing is decided (TAD 13).

## What the employee does
Nothing. After IT installs WinAgent, the PC asks to connect on its own. When the Admin starts setup for that
employee, the employee sees one **Approve** button on their dashboard, enters their password, and clicks it.
Monitoring then starts in the background. No code, no window, no terminal.

## Build the installer (once, on a build machine)
1. Run `build.ps1` in the project root. This tests the agent and creates `dist\winagent\`.
2. Install Inno Setup 6, then compile: `ISCC.exe installer\winagent.iss`.
3. The result is `dist\WinAgentSetup-0.1.0.exe`.

## Silent install command
Run it in the employee's own user session (the install is per user, with no admin rights):

```bash
WinAgentSetup-0.1.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /APIURL=https://monitoring.example.com/api/agent/v1
```

Add `/CAFILE=C:\path\to\company-ca.pem` only when the server uses a private certificate authority.
`/APIURL` must be HTTPS. Without it the installer does nothing and reports an error.

## What the installer does
- Copies WinAgent to `%LOCALAPPDATA%\WinAgent\app`. No admin rights, no UAC prompt.
- Writes `config.toml` with the server address. The employee never edits it.
- Turns on start at sign-in (per-user Run key).
- Starts the agent hidden. With no credential yet, it asks the server to connect and waits for approval.
- Uninstall (Settings > Apps) turns start at sign-in off.

## Deployment options
| Tool | How | Notes |
|---|---|---|
| Intune (user context) | Win32 app with the install command above, install behaviour "User" | Runs at each user's sign-in until installed |
| Group Policy logon script | User logon script that runs the install command | Needs the file on a share that users can read |
| Remote management (RMM) | Run the command in the logged-on user's session | Check it runs as that user, not SYSTEM |

Per-user installs must run in the user's own session. Running as SYSTEM would install it for the wrong profile.

## Before rollout
- Test on one clean Windows 11 user account: silent install, no windows, agent starts, pairing request appears,
  Approve works, uninstall removes start at sign-in.
- Sign the installer and `winagent.exe` (TAD 13 decision). Unsigned files trigger Windows warnings and may be
  blocked by policy.

## Admin Deployment page

In the Admin dashboard, open **Deployment** in the sidebar. It shows the install command for your server, a button to make the organization's installer token (shown once), and a download of `Deploy-WinAgent.ps1`. Give IT the token and the script, then push it to PCs as the signed-in user (Intune "User" context, a logon script, or an RMM tool). After the install, use **Connect a new PC** on the employee's page so the employee can approve the PC on their dashboard.

## Build and code signing

`build.ps1` does the whole build: tests, PyInstaller, Inno Setup, and a copy of the installer to `backend\agent_dist\WinAgentSetup.exe`, where the Admin Deployment page serves it (to logged-in Admins only). `build.ps1 -CheckOnly` checks the tools and signing settings without building.

To sign `winagent.exe` and the installer, set these environment variables before the build:

| Variable | Meaning |
|---|---|
| `PFX_PATH` | Path to the code signing certificate (.pfx) |
| `PFX_PASSWORD` | The certificate password (never printed) |
| `TIMESTAMP_URL` | Timestamp server (default `http://timestamp.digicert.com`) |
| `SIGNTOOL_PATH` | Optional full path to `signtool.exe` (otherwise PATH and the Windows SDK are searched) |

With no `PFX_PATH` the build is unsigned and says so. With `PFX_PATH` set, any signing problem stops the build, so a build that was meant to be signed is never released unsigned.

## Private certificate authority

The agent always verifies the server certificate. For a server with a private or self-signed certificate, pass the CA file to the installer (`/CAFILE=C:\path\ca.pem`) or the script (`-CaFile`). It is saved as `ca_bundle` in the agent's `config.toml` and only changes which authority is trusted. There is no setting to turn verification off.
