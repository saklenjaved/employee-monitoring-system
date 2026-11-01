"""Static privacy checks (privacy rule, TAD 1.2, 11). Scans the source tree for forbidden APIs.

If one of these checks fails, the code must be removed or reviewed by agent-reviewer.
"""

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parent.parent / "winagent"

FORBIDDEN = {
    "keyboard hook": r"SetWindowsHookEx|WH_KEYBOARD|WH_KEYBOARD_LL",
    "key state polling": r"GetAsyncKeyState|GetKeyState\b|GetKeyboardState",
    "raw input": r"RegisterRawInputDevices|WM_INPUT\b|WM_KEYDOWN|WM_CHAR\b",
    "clipboard read": r"GetClipboardData|OpenClipboard|pyperclip",
    "keyboard library": r"\bpynput\b|^\s*(import|from)\s+keyboard\b",
    "webcam or audio": r"\bcv2\b|VideoCapture|sounddevice|pyaudio|\bwinsound\b",
    "continuous recording": r"ffmpeg|imageio|screen_?record",
}


def _source_files():
    return [p for p in SRC.rglob("*.py")]


@pytest.mark.parametrize("label,pattern", FORBIDDEN.items())
def test_no_forbidden_api(label, pattern):
    hits = []
    for path in _source_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if re.search(pattern, line):
                hits.append(f"{path.name}:{lineno}")
    assert hits == [], f"{label} found in {hits}"


def _property_names(node, found):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                found.update(value.keys())
            _property_names(value, found)
    elif isinstance(node, list):
        for item in node:
            _property_names(item, found)
    return found


def test_event_schema_has_no_text_or_ownership_fields():
    import json

    schema_path = Path(__file__).resolve().parent.parent / "docs" / "event-schema.json"
    names = {n.lower() for n in _property_names(json.loads(schema_path.read_text(encoding="utf-8")), set())}
    for banned in ("keystroke", "keys", "typed_text", "text", "clipboard", "organization_id", "employee_id"):
        assert banned not in names, f"schema must not define property {banned!r}"
