"""Windows implementations of the platform interfaces (TAD 5.2).

Every Windows import happens inside a function or method, so this module imports on Linux
and CI can use the fakes in tests/fakes.py instead. Nothing here reads keyboard input,
key codes, clipboard or typed text (privacy rule).
"""

from __future__ import annotations

import ctypes
import io
from ctypes import wintypes

from winagent.platform_base import ForegroundInfo

AUTOSTART_VALUE_NAME = "WinAgent"
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
SM_REMOTESESSION = 0x1000
CRYPTPROTECT_UI_FORBIDDEN = 0x01


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
    buf = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


class DpapiProtector:
    """Per-user DPAPI. No LOCAL_MACHINE flag, so only this Windows user can decrypt."""

    def protect(self, data: bytes) -> bytes:
        return self._call(data, protect=True)

    def unprotect(self, blob: bytes) -> bytes:
        return self._call(blob, protect=False)

    def _call(self, data: bytes, protect: bool) -> bytes:
        crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        in_blob, _keep = _blob(data)
        out_blob = _DataBlob()
        fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
        ok = fn(ctypes.byref(in_blob), None, None, None, None, CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob))
        if not ok:
            raise OSError("DPAPI call failed")
        try:
            return ctypes.string_at(out_blob.pbData, out_blob.cbData)
        finally:
            kernel32.LocalFree(out_blob.pbData)


class Win32ForegroundWindowReader:
    def read(self, include_title: bool) -> ForegroundInfo | None:
        import psutil
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            name = psutil.Process(pid).name()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None
        title = win32gui.GetWindowText(hwnd) if include_title else None
        return ForegroundInfo(process_name=name, window_title=title)


class Win32IdleTimeReader:
    """GetLastInputInfo returns a tick timestamp. It carries no key or content data."""

    class _LastInputInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    def idle_seconds(self) -> float:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        info = self._LastInputInfo()
        info.cbSize = ctypes.sizeof(info)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            raise OSError("GetLastInputInfo failed")
        now = kernel32.GetTickCount()
        return ((now - info.dwTime) & 0xFFFFFFFF) / 1000.0


class MssScreenCapturer:
    """Captures the primary monitor as PNG bytes in memory. Caller decides where bytes go."""

    def capture_png(self) -> bytes:
        import mss
        from PIL import Image

        with mss.mss() as sct:
            shot = sct.grab(sct.monitors[1])
            image = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        out = io.BytesIO()
        image.save(out, format="PNG", optimize=True)
        return out.getvalue()


class Win32SessionInspector:
    def session_type(self) -> str:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        if not user32.GetSystemMetrics(SM_REMOTESESSION):
            return "console"
        import psutil

        names = {p.info["name"] for p in psutil.process_iter(["name"]) if p.info["name"]}
        if "wfica32.exe" in names or "concentr.exe" in names:
            return "citrix"
        return "rdp"

    def is_locked(self) -> bool:
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        desktop = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
        if not desktop:
            return True  # secure desktop (lock screen) is active
        user32.CloseDesktop(desktop)
        return False


class WinregAutostart:
    """Per-user Run key (HKCU). No admin rights. Installer approach is still open (TAD 13)."""

    def enable(self, command: str) -> None:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, AUTOSTART_VALUE_NAME, 0, winreg.REG_SZ, command)

    def disable(self) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, AUTOSTART_VALUE_NAME)
        except FileNotFoundError:
            pass

    def is_enabled(self) -> bool:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0, winreg.KEY_READ) as key:
                winreg.QueryValueEx(key, AUTOSTART_VALUE_NAME)
                return True
        except FileNotFoundError:
            return False
