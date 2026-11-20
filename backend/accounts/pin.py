"""The employee's monitoring PIN.

Only a salted hash is stored. Wrong guesses lock the PIN for a while, and the counter lives in the
database so every server process sees it. The PIN is never logged and never sent to the agent.
"""
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from audit.services import record_event

from .models import MonitoringPin

PIN_MAX_FAILURES = 5
PIN_LOCK_MINUTES = 15

PIN_OK = "ok"
PIN_WRONG = "wrong"
PIN_LOCKED = "locked"
PIN_MISSING = "missing"


def has_pin(user):
    return MonitoringPin.objects.filter(user=user).exclude(pin_hash="").exists()


def set_pin(user, pin):
    """Save a new PIN for `user`. The caller has already checked the format and the current password."""
    with transaction.atomic():
        record, _ = MonitoringPin.objects.select_for_update().get_or_create(
            user=user, defaults={"organization": user.organization, "pin_hash": ""}
        )
        record.pin_hash = make_password(pin)
        record.failed_attempts = 0
        record.locked_until = None
        record.save()
        record_event(user, "monitoring_pin.set", organization=user.organization, target=record)


def check_pin(user, pin, now=None):
    """Return PIN_OK, PIN_WRONG, PIN_LOCKED or PIN_MISSING. Counts a wrong guess and locks after too many."""
    now = now or timezone.now()
    with transaction.atomic():
        record = MonitoringPin.objects.select_for_update().filter(user=user).first()
        if record is None or not record.pin_hash:
            return PIN_MISSING
        if record.locked_until and record.locked_until > now:
            return PIN_LOCKED
        if check_password(pin, record.pin_hash):
            record.failed_attempts = 0
            record.locked_until = None
            record.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
            return PIN_OK
        record.failed_attempts += 1
        if record.failed_attempts >= PIN_MAX_FAILURES:
            record.failed_attempts = 0
            record.locked_until = now + timedelta(minutes=PIN_LOCK_MINUTES)
            record_event(user, "monitoring_pin.locked", organization=user.organization, target=record)
        record.save(update_fields=["failed_attempts", "locked_until", "updated_at"])
        return PIN_LOCKED if record.locked_until else PIN_WRONG
