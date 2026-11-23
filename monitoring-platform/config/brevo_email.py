"""Sends Django email through Brevo's HTTPS API (https://api.brevo.com/v3/smtp/email).

Render's free web services block outbound SMTP ports (25, 465, 587), so mail goes over HTTPS (port 443)
instead. Only Python's standard library is used. The API key comes from BREVO_API_KEY and is never logged.
The sender address (DEFAULT_FROM_EMAIL) must be a sender that is verified in the Brevo account.
"""

import json
import logging
import re
import urllib.error
import urllib.request
from email.utils import parseaddr

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)

BREVO_URL = "https://api.brevo.com/v3/smtp/email"
EMAIL_ADDRESS = re.compile(r"[^\s@<>]+@[^\s@<>]+")


def _safe_detail(error):
    """The error code and message Brevo gave, cut down so they are safe to log.

    Only the "code" and "message" fields are used, never the raw response. Email addresses are removed and
    both values are shortened. Returns ("", "") when the response is not the expected JSON.
    """
    try:
        data = json.loads(error.read(2048))
        code, message = str(data.get("code", "")), str(data.get("message", ""))
    except (OSError, ValueError, AttributeError):
        return "", ""
    code = re.sub(r"[^A-Za-z0-9_.-]", "", code)[:50]
    message = EMAIL_ADDRESS.sub("<email>", message)
    message = " ".join(message.split())[:200]
    return code, message


class BrevoEmailBackend(BaseEmailBackend):
    def send_messages(self, email_messages):
        sent = 0
        for message in email_messages:
            self._send(message)
            sent += 1
        return sent

    def _send(self, message):
        name, address = parseaddr(message.from_email or settings.DEFAULT_FROM_EMAIL)
        sender = {"email": address}
        if name:
            sender["name"] = name
        payload = {
            "sender": sender,
            "to": [{"email": recipient} for recipient in message.to],
            "subject": message.subject,
            "textContent": message.body,
        }
        for content, mimetype in getattr(message, "alternatives", []):
            if mimetype == "text/html":
                payload["htmlContent"] = content
        request = urllib.request.Request(
            BREVO_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "api-key": settings.BREVO_API_KEY,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "employee-monitoring/1.0",
            },
            method="POST",
        )
        # An HTTP error (wrong key, unverified sender) or a network error is raised, so the caller can report it.
        # The wait is limited by EMAIL_TIMEOUT, so a page never hangs on the mail service.
        try:
            with urllib.request.urlopen(request, timeout=settings.EMAIL_TIMEOUT) as response:
                response.read()
        except urllib.error.HTTPError as error:
            # Log what Brevo said (status, code, short message) so a wrong key or sender can be told apart.
            # Never the key, the address, the message text or the raw response.
            code, detail = _safe_detail(error)
            logger.warning("Brevo refused the email: HTTP %s, code %r, message %r", error.code, code, detail)
            raise
        except urllib.error.URLError as error:
            logger.warning("Brevo could not be reached (%s)", type(error.reason).__name__)
            raise
