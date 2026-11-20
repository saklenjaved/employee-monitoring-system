"""A small window for the employee: paste the code from the dashboard and click Connect.

The employee does not need a command line or a config file. The window only shows
plain messages. It never shows the code after it has been sent.
"""

from __future__ import annotations

from typing import Callable

from winagent.http_client import HttpError, TransientError
from winagent.registration import RegistrationError

WINDOW_TITLE = "WinAgent setup"
BAD_CODE_MESSAGE = "That code did not work. Check it, or get a new code from your dashboard."
NO_SERVER_MESSAGE = "Could not reach the server. Check the internet connection and try again."


def friendly_error(exc: Exception) -> str:
    """Turn a registration failure into a short message for the employee."""
    if isinstance(exc, RegistrationError):
        return str(exc)
    if isinstance(exc, TransientError) or (isinstance(exc, HttpError) and exc.status is None):
        return NO_SERVER_MESSAGE
    return BAD_CODE_MESSAGE


def ask_for_code(register: Callable[[str], str | None]) -> bool:
    """Show the window until the code works or the employee closes it.

    `register` takes the pasted code and returns None when the device is connected, or a
    message to show when it is not. Returns True when the device was connected.
    """
    import tkinter as tk

    root = tk.Tk()
    root.title(WINDOW_TITLE)
    root.resizable(False, False)
    connected = {"ok": False}

    frame = tk.Frame(root, padx=20, pady=20)
    frame.pack()
    tk.Label(frame, text="Paste the code from your dashboard", font=("Segoe UI", 12, "bold")).pack(anchor="w")
    tk.Label(
        frame,
        text="On the dashboard, open 'Connect this PC', copy the code and paste it here.",
        wraplength=380,
        justify="left",
    ).pack(anchor="w", pady=(4, 12))

    code_var = tk.StringVar()
    entry = tk.Entry(frame, textvariable=code_var, width=24, font=("Consolas", 14))
    entry.pack(anchor="w")
    entry.focus_set()

    status = tk.Label(frame, text="", fg="#b00020", wraplength=380, justify="left")
    status.pack(anchor="w", pady=(8, 0))
    button = tk.Button(frame, text="Connect")
    button.pack(anchor="e", pady=(12, 0))

    def connect(_event=None):
        code = code_var.get().strip()
        if not code:
            status.config(text="Paste the code first.", fg="#b00020")
            return
        button.config(state="disabled")
        status.config(text="Connecting...", fg="#333333")
        root.update_idletasks()
        error = register(code)
        if error is None:
            connected["ok"] = True
            root.destroy()
            return
        status.config(text=error, fg="#b00020")
        button.config(state="normal")

    button.config(command=connect)
    entry.bind("<Return>", connect)
    root.mainloop()
    return connected["ok"]
