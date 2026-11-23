"""One-time activation codes for invited people.

A code is 8 random digits, sent by email. The database keeps only a keyed hash of it
(HMAC-SHA256 with the server's secret key), so a copy of the database cannot be used to
activate an account. A code works once, expires after 24 hours, and locks after too many
wrong tries. Sending a new code makes the old unused one invalid.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from .models import ActivationCode

CODE_DIGITS = 8
CODE_LIFETIME = timedelta(hours=24)
MAX_WRONG_TRIES = 5

# Results of check_code. The views turn them into messages.
OK = "ok"
WRONG = "wrong"
EXPIRED = "expired"
LOCKED = "locked"
MISSING = "missing"


def new_code():
    """A random code with leading zeros kept, for example "04719283"."""
    return f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"


def _keyed_hash(membership_pk, code):
    # The membership ID is part of the message, so a code for one invitation cannot be used for another.
    message = f"activation:{membership_pk}:{code}".encode("utf-8")
    return hmac.new(settings.SECRET_KEY.encode("utf-8"), message, hashlib.sha256).hexdigest()


def issue_code(membership):
    """Store a new code for `membership`, replacing any unused one. Returns the plain code.

    The plain code is only returned so it can be emailed. It is never stored or logged.
    """
    code = new_code()
    ActivationCode.objects.filter(membership=membership, used_at__isnull=True).delete()
    ActivationCode.objects.create(
        membership=membership,
        code_hash=_keyed_hash(membership.pk, code),
        expires_at=timezone.now() + CODE_LIFETIME,
    )
    return code


def latest_code(membership):
    return ActivationCode.objects.filter(membership=membership).order_by("-created_at", "-pk").first()


def check_code(membership, code):
    """Check a typed code for a pending membership. Returns (result, tries_left).

    A wrong code uses up one try. The comparison is constant-time.
    """
    current = latest_code(membership)
    if current is None:
        return MISSING, 0
    if current.used_at is not None:
        return MISSING, 0
    if current.attempts >= MAX_WRONG_TRIES:
        return LOCKED, 0
    if timezone.now() >= current.expires_at:
        return EXPIRED, 0
    if hmac.compare_digest(current.code_hash, _keyed_hash(membership.pk, code)):
        return OK, MAX_WRONG_TRIES - current.attempts
    ActivationCode.objects.filter(pk=current.pk).update(attempts=F("attempts") + 1)
    tries_left = MAX_WRONG_TRIES - current.attempts - 1
    return (LOCKED if tries_left <= 0 else WRONG), max(tries_left, 0)


def code_matches(membership, code):
    """True when `code` is the membership's latest code. It does not use up a try and ignores expiry.

    Used to find which of several invitations (the same email in several organizations) a code belongs to.
    check_code then decides whether that code is still valid.
    """
    current = latest_code(membership)
    return current is not None and hmac.compare_digest(current.code_hash, _keyed_hash(membership.pk, code))


def check_code_for_any(memberships, code):
    """Check a code against several pending memberships of the same email. Returns (membership, result, tries_left).

    The membership whose code matches is checked normally. When no code matches, the wrong try counts against
    every one of them, so having invitations in several organizations never gives extra guesses.
    """
    for membership in memberships:
        if code_matches(membership, code):
            result, tries_left = check_code(membership, code)
            return membership, result, tries_left
    results = [check_code(membership, code) for membership in memberships]
    for wanted in (WRONG, EXPIRED, LOCKED):
        found = [tries for result, tries in results if result == wanted]
        if found:
            return None, wanted, min(found)
    return None, MISSING, 0


def mark_used(membership):
    """Use up the current code. Returns False if it was already used (a concurrent request won)."""
    updated = ActivationCode.objects.filter(membership=membership, used_at__isnull=True).update(used_at=timezone.now())
    return updated == 1
