"""Transfers of a person between organizations (Super Admin only).

1. The Super Admin starts a transfer. The person gets an 8-digit code by email.
2. The person enters the code on the transfer page. Only then does the old membership end
   (as "transferred") and a pending membership open in the destination, without a role.
3. The destination Admin assigns the role (accounts.services.assign_role).

The User account, email and password never change. The old membership and everything
recorded under it stay as history in the old organization. Steps 2 and 3 are run by
workforce.lifecycle, which also revokes the old organization's devices and team links.
"""
from django.conf import settings
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from audit.services import record_event

from .activation import CODE_LIFETIME, EXPIRED, LOCKED, OK, WRONG, check_code_for_any, issue_code, mark_used
from .models import ActivationCode, EndReason, Membership, MembershipStatus, Role, Transfer, TransferStatus, User
from .ratelimit import hit, key_for, too_many
from .services import ASSIGNABLE_ROLES, LIMITED_MESSAGE, logger, too_many_code_attempts

START_LIMIT = 3  # transfer emails per person per hour
START_WINDOW_SECONDS = 60 * 60

NO_PERSON_MESSAGE = "No active Employee or Manager has this email."
SEVERAL_ACCOUNTS_MESSAGE = (
    "This email has active accounts in more than one organization, so a transfer cannot tell which one to move."
)
INVALID_MESSAGE = "The code is not valid. Check the email address and code, or ask the Super Admin for a new code."
EXPIRED_MESSAGE = "This code has expired. Ask the Super Admin to send a new code."
LOCKED_MESSAGE = "This code is locked after too many wrong tries. Ask the Super Admin to send a new code."
DONE_MESSAGE = "This transfer was already confirmed. Your new administrator will assign your role."


def start_transfer(actor, email, destination):
    """Ask to move the person with `email` to `destination`. Returns (transfer, plain code).

    A newer request replaces a waiting one, and its new code replaces the old code.
    Raises ValueError when the transfer is not possible. The caller emails the code after this returns.
    """
    if actor.role != Role.SUPER_ADMIN:
        raise ValueError("Only the Super Admin can transfer people.")
    email = email.strip().lower()
    # Each organization has its own account for an email. A transfer moves one account, so it must be clear which.
    sources = list(
        Membership.objects.filter(user__email=email, status=MembershipStatus.ACTIVE, role__in=ASSIGNABLE_ROLES)
        .select_related("organization", "user")
    )
    if not sources:
        raise ValueError(NO_PERSON_MESSAGE)
    if len(sources) > 1:
        raise ValueError(SEVERAL_ACCOUNTS_MESSAGE)
    source = sources[0]
    user = source.user
    if not destination.is_active:
        raise ValueError("The destination organization is deactivated.")
    if destination.pk == source.organization_id:
        raise ValueError("The person already belongs to that organization.")
    if User.objects.filter(email=email, organization=destination).exists():
        raise ValueError("This email already has an account in the destination organization.")
    key = key_for("transfer-start", str(user.pk))
    if too_many(key, START_LIMIT):
        raise ValueError("Too many transfer codes were sent for this person. Try again later.")
    hit(key, START_WINDOW_SECONDS)
    with transaction.atomic():
        Transfer.objects.filter(user=user, status=TransferStatus.AWAITING_CODE).update(status=TransferStatus.CANCELLED)
        transfer = Transfer.objects.create(
            user=user, source_membership=source, destination_organization=destination, requested_by=actor
        )
        code = issue_code(source)
        detail = f"from organization {source.organization_id} to organization {destination.pk}"
        # Recorded in both organizations, so each Admin's audit log shows the move.
        record_event(actor, "membership.transfer_requested", organization=source.organization, target=user, detail=detail)
        record_event(actor, "membership.transfer_requested", organization=destination, target=user, detail=detail)
    return transfer, code


def cancel_transfer(actor, transfer):
    """Stop a waiting transfer. Its code stops working. Nothing else changes."""
    if transfer.status != TransferStatus.AWAITING_CODE:
        raise ValueError("Only a transfer that waits for the code can be cancelled.")
    with transaction.atomic():
        transfer.status = TransferStatus.CANCELLED
        transfer.save(update_fields=["status"])
        ActivationCode.objects.filter(membership=transfer.source_membership, used_at__isnull=True).delete()
        record_event(
            actor,
            "membership.transfer_cancelled",
            organization=transfer.source_membership.organization,
            target=transfer.user,
            detail=f"to organization {transfer.destination_organization_id}",
        )


def send_transfer_code(request, transfer, code):
    """Email the transfer code to the person. Returns True if sent. The code is never logged."""
    context = {
        "code": code,
        "email": transfer.user.email,
        "source_name": transfer.source_membership.organization.name,
        "destination_name": transfer.destination_organization.name,
        "verify_url": request.build_absolute_uri(reverse("transfer_verify")),
        "expiry_hours": int(CODE_LIFETIME.total_seconds() // 3600),
    }
    subject = render_to_string("accounts/transfer_subject.txt", context).strip()
    body = render_to_string("accounts/transfer_email.txt", context)
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [transfer.user.email])
    except Exception as error:  # The Super Admin can start the transfer again to send a new code.
        logger.warning("transfer email failed for transfer %s (%s)", transfer.pk, type(error).__name__)
        return False
    return True


def check_transfer_code(request, email, code):
    """Check the person's code for their waiting transfer. Returns (transfer, error_message).

    A wrong code uses up one try. Nothing changes yet; finish_transfer does the move.
    """
    email = email.strip().lower()
    if too_many_code_attempts(request, "transfer", email):
        return None, LIMITED_MESSAGE
    # Only one account per email can be waiting (start_transfer refuses an email with several active accounts),
    # but the lookup is by email across organizations, so it is done the same way as activation.
    transfers = list(
        Transfer.objects.filter(user__email=email, status=TransferStatus.AWAITING_CODE)
        .select_related("source_membership__organization", "destination_organization", "user")
        .order_by("created_at", "pk")
    )
    if not transfers:
        if Transfer.objects.filter(user__email=email, status=TransferStatus.COMPLETED).exists():
            return None, DONE_MESSAGE
        return None, INVALID_MESSAGE
    by_source = {transfer.source_membership_id: transfer for transfer in transfers}
    source, result, tries_left = check_code_for_any([transfer.source_membership for transfer in transfers], code)
    transfer = by_source[source.pk] if source is not None else None
    if transfer is not None and (source.status != MembershipStatus.ACTIVE or not transfer.destination_organization.is_active):
        return None, INVALID_MESSAGE
    if result == WRONG:
        return None, f"The code is not correct. Tries left before it locks: {tries_left}."
    if result == LOCKED:
        return None, LOCKED_MESSAGE
    if result == EXPIRED:
        return None, EXPIRED_MESSAGE
    if result != OK:
        return None, INVALID_MESSAGE
    return transfer, ""


def finish_transfer(transfer):
    """Use up the code, end the old membership and open the pending destination membership.

    Returns the destination membership, or None when another request used the code first.
    Call inside a transaction, together with the clean-up of the old organization's access.
    """
    if not mark_used(transfer.source_membership):
        return None
    now = timezone.now()
    source = transfer.source_membership
    source.status = MembershipStatus.ENDED
    source.end_reason = EndReason.TRANSFERRED
    source.ended_at = now
    source.save(update_fields=["status", "end_reason", "ended_at"])
    user = transfer.user
    # Pending with no role: the destination Admin chooses it (accounts.services.assign_role).
    destination = Membership.objects.create(
        user=user,
        organization=transfer.destination_organization,
        role="",
        status=MembershipStatus.PENDING,
        created_by=transfer.requested_by,
    )
    user.organization = transfer.destination_organization
    user.is_active = False  # no sign-in until the role is assigned
    user.save(update_fields=["organization", "is_active"])
    transfer.status = TransferStatus.COMPLETED
    transfer.completed_at = now
    transfer.destination_membership = destination
    transfer.save(update_fields=["status", "completed_at", "destination_membership"])
    by = f"requested by user {transfer.requested_by_id}"
    record_event(
        user,
        "membership.transferred",
        organization=source.organization,
        target=user,
        detail=f"to organization {destination.organization_id}, {by}",
    )
    record_event(
        user,
        "membership.transferred_in",
        organization=destination.organization,
        target=user,
        detail=f"from organization {source.organization_id}, {by}",
    )
    return destination
