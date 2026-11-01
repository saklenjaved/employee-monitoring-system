"""Status indicator in the system tray (TAD 1.1: the employee can see the agent is installed and its status).

Shows state only. It has no buttons that change monitoring. Quitting stops the agent process,
which the employee can do at any time. Requires pystray and Pillow on Windows.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

from winagent.health import HealthSnapshot, status_line

log = logging.getLogger("winagent.tray")


def _icon_image():
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((6, 6, 58, 58), fill=(37, 99, 235, 255))
    return img


def start_tray(stop: threading.Event, get_health: Callable[[], HealthSnapshot]) -> threading.Thread | None:
    try:
        import pystray
    except ImportError:
        log.info("pystray not installed; running without a tray icon")
        return None

    def on_quit(icon, _item):
        stop.set()
        icon.stop()

    def label(_item=None) -> str:
        return status_line(get_health())

    icon = pystray.Icon(
        "winagent",
        _icon_image(),
        "Workplace monitoring agent",
        menu=pystray.Menu(
            pystray.MenuItem(label, None, enabled=False),
            pystray.MenuItem("Quit agent", on_quit),
        ),
    )
    thread = threading.Thread(target=icon.run, name="tray", daemon=True)
    thread.start()
    return thread
