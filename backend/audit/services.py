from .models import AuditLog


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
