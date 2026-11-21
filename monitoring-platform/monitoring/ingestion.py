"""Storing events sent by a Windows agent.

Organization and employee come from the device record. A resent event is
recognised by its event_id (unique per device) and is not stored twice.
"""
from django.db import transaction
from django.utils import timezone

from .models import MonitoringEvent
from .validation import is_event_id, validate_event

MAX_EVENTS_PER_BATCH = 500


def ingest_events(device, raw_events):
    """Validate and store a batch. Returns {"accepted": [...], "duplicates": [...], "rejected": [...]}.

    Every list holds event IDs. An event without a valid ID cannot be named in the
    answer, so it is skipped.
    """
    now = timezone.now()
    accepted, duplicates, rejected = [], [], []
    candidates = {}  # event_id -> clean event, in the order received
    refused = set()

    for raw in raw_events:
        event_id = raw.get("event_id") if isinstance(raw, dict) else None
        if not is_event_id(event_id):
            continue
        if event_id in candidates:
            duplicates.append(event_id)  # the same event twice in one batch
            continue
        if event_id in refused:
            rejected.append(event_id)
            continue
        clean = validate_event(raw, now)
        if clean is None:
            refused.add(event_id)
            rejected.append(event_id)
        else:
            candidates[event_id] = clean

    # Only this device's own events count as already stored.
    already_stored = {
        str(stored_id)
        for stored_id in MonitoringEvent.objects.filter(device=device, event_id__in=list(candidates)).values_list(
            "event_id", flat=True
        )
    }
    new_events = []
    for event_id, clean in candidates.items():
        if event_id in already_stored:
            duplicates.append(event_id)
            continue
        accepted.append(event_id)
        new_events.append(
            MonitoringEvent(
                organization_id=device.organization_id,
                user_id=device.user_id,
                device=device,
                event_id=event_id,
                event_type=clean["event_type"],
                captured_at=clean["captured_at"],
                payload=clean["payload"],
            )
        )
    with transaction.atomic():
        # If two requests carry the same event at once, the database keeps one copy.
        MonitoringEvent.objects.bulk_create(new_events, ignore_conflicts=True)
    return {"accepted": accepted, "duplicates": duplicates, "rejected": rejected}
