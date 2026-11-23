from django.core.exceptions import PermissionDenied
from django.db import transaction

from accounts.models import Role

from .models import AuditDeletion, AuditLog

DELETE_BATCH_SIZE = 1000  # rows per DELETE statement, so a very large deletion stays fast and light


def record_event(actor, action, organization=None, target=None, detail=""):
    """Add one audit record. Call it in the same transaction as the action it records."""
    return AuditLog.objects.create(
        actor=actor,
        actor_email=actor.email if actor else "",
        action=action,
        organization=organization,
        target_type=target._meta.label if target is not None else "",
        target_id=str(target.pk) if target is not None else "",
        detail=detail,
    )


def delete_entries(actor, entries, method):
    """Delete audit entries. Only the Super Admin may, and every deletion leaves an AuditDeletion record.

    `entries` is a queryset of AuditLog rows chosen by the caller. The rows are removed in batches and the
    record (who, when, how many, how) is written in the same transaction. Returns how many were deleted.
    Nothing else is deleted: accounts, organizations and other data are not touched.
    """
    if actor is None or getattr(actor, "role", None) != Role.SUPER_ADMIN:
        raise PermissionDenied
    with transaction.atomic():
        ids = list(entries.order_by().values_list("pk", flat=True))
        deleted = 0
        for start in range(0, len(ids), DELETE_BATCH_SIZE):
            batch = AuditLog.objects.filter(pk__in=ids[start : start + DELETE_BATCH_SIZE])
            deleted += batch.delete_by_service()[0]
        if deleted:
            AuditDeletion.objects.create(actor=actor, actor_email=actor.email, entry_count=deleted, method=method)
    return deleted
