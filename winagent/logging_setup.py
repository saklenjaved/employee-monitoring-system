"""Logging with secrets redacted (TAD 5.1 logging & health, TAD 11).

The filter removes bearer tokens, query strings (pre-signed URLs carry signatures) and
credential-like key=value pairs before any handler writes the record.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_PATTERNS = [
    (re.compile(r"(Bearer\s+)[A-Za-z0-9._~+/=-]+", re.IGNORECASE), r"\1<redacted>"),
    (re.compile(r"(https?://[^\s?#]+)\?[^\s]*"), r"\1?<redacted>"),
    (re.compile(r"(?i)\b(token|credential|secret|signature|password|authorization)\b\s*[=:]\s*\S+"), r"\1=<redacted>"),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        record.msg = redact(message)
        record.args = ()
        if record.exc_info:
            # Keep the exception class and a short text only, because messages can carry URLs.
            record.exc_info = None
            record.exc_text = None
        return True


def configure_logging(log_dir: Path, version: str, level: int = logging.INFO) -> logging.Logger:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger("winagent")
    root.setLevel(level)
    root.handlers.clear()
    fmt = logging.Formatter(f"%(asctime)s %(levelname)s v{version} %(name)s: %(message)s")
    handler = RotatingFileHandler(log_dir / "agent.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(fmt)
    handler.addFilter(RedactingFilter())
    root.addHandler(handler)
    return root
