# ADR 0001: Python stack and libraries

Status: accepted for POC.

- Python 3.11 or newer (tested on 3.14 in the dev venv; 3.11 install was blocked by a launcher path on the dev machine).
- Windows calls: pywin32, ctypes (GetLastInputInfo, DPAPI), psutil, mss, Pillow, winreg. Tray: pystray.
- HTTP: requests with explicit `verify=True`.
- All Windows calls sit behind Protocols in `platform_base.py`, so tests run on Linux with fakes.
- Why: TAD 5.2 recommends these; the Protocol seam keeps them testable.
