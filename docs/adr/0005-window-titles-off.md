# ADR 0005: Window titles are on, with exclusions

Status: accepted (default, to confirm).

- `capture_window_title = true`. Titles are cut to 256 characters.
- Exclusions: apps on `excluded_apps` (KeePass, 1Password, Bitwarden) produce no events at all. Titles that contain a sensitive keyword (password, incognito, inprivate, private browsing) are dropped; the app event is kept.
- Reason: the brief asks for window titles. Titles can still hold private content, so the exclusions are the protection.
