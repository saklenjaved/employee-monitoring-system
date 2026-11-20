"""Keep invitation and password-setup tokens out of the request logs.

The set-password link carries its one-time token in the URL path:
/password-reset/<uidb64>/<token>/. Django's development server and its request-error log write
the full path, so the token would end up in the terminal. This filter replaces the token with
"<redacted>" before any log line is written. The link itself and its token checks are unchanged.
"""
import logging
import re

# Matches the token segment, but not the "set-password" step that Django shows after the first click.
TOKEN_IN_PATH = re.compile(r"(/password-reset/[^/\s\"']+/)(?!set-password/)[^/\s\"']+/")
REDACTED = r"\1<redacted>/"


def redact(text):
    return TOKEN_IN_PATH.sub(REDACTED, text)


class RedactResetTokens(logging.Filter):
    def filter(self, record):
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if record.args:
            record.args = tuple(redact(arg) if isinstance(arg, str) else arg for arg in record.args)
        return True


def install():
    """Attach the filter to the loggers that record request paths. Safe to call more than once."""
    for name in ("django.server", "django.request"):
        logger = logging.getLogger(name)
        if not any(isinstance(item, RedactResetTokens) for item in logger.filters):
            logger.addFilter(RedactResetTokens())
