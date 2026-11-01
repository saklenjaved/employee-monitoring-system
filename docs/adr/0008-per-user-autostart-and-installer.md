# ADR 0008: Per-user auto-start; installer technology open

Status: accepted for POC (auto-start). Installer technology is open.

- Auto-start: `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, value `WinAgent`. No admin rights (ASSUMPTION: TAD 13 leaves the method open).
- Installer: not chosen. The build produces a PyInstaller one-folder zip. See `build.ps1`.
