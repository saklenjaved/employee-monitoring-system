"""Business rules for user accounts.

Every function takes the logged-in user (`viewer`) as an argument. The
organization always comes from that user on the server, never from request data.
"""
import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core import signing
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Case, CharField, OuterRef, Subquery, Value, When
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from audit.services import record_event

from .activation import CODE_LIFETIME, EXPIRED, LOCKED, OK, WRONG, check_code, check_code_for_any, issue_code, mark_used
from .models import ActivationCode, Membership, MembershipStatus, Role, Transfer, TransferStatus, User
from .ratelimit import client_ip, hit, key_for, too_many

logger = logging.getLogger(__name__)

INVITE_SALT = "accounts.activation-invite"  # a signature for this purpose only


def invite_token(user):
    """A signed token for the activation link. It holds only the account's ID, never the email address."""
    return signing.dumps(user.pk, salt=INVITE_SALT)


def invited_email(token):
    """The email address for a token from an activation link, or "" if the token is missing or tampered with."""
    if not token:
        return ""
    try:
        pk = signing.loads(token, salt=INVITE_SALT)
    except signing.BadSignature:
        return ""
    return User.objects.filter(pk=pk).values_list("email", flat=True).first() or ""

RESEND_INVITE_LIMIT = 3  # resends per account
RESEND_INVITE_WINDOW_SECONDS = 60 * 60


def visible_users(viewer):
    """Return the users that `viewer` is allowed to see."""
    if viewer.role == Role.SUPER_ADMIN:
        # The Super Admin manages organization Admin accounts, not employee data.
        return User.objects.filter(role=Role.ADMIN)
    if viewer.role == Role.ADMIN:
        return User.objects.filter(organization=viewer.organization)
    # Managers see only themselves until team assignments exist (workforce app).
    # Employees see only themselves.
    return User.objects.filter(pk=viewer.pk)


def get_visible_user(viewer, user_id):
    """Return one user the viewer may see, or None.

    The lookup uses the ID and the viewer's scope together, so another
    organization's user is simply "not found".
    """
    return visible_users(viewer).filter(pk=user_id).first()


def platform_admins(viewer):
    """Every Admin account of every organization, for the Super Admin. Other roles get nothing.

    `admin_status` is "pending" (invited, not activated yet), "inactive" (deactivated, or the Admin's
    membership ended) or "active". It comes from the Admin's latest Admin membership.
    """
    if viewer.role != Role.SUPER_ADMIN:
        return User.objects.none()
    latest = Membership.objects.filter(user=OuterRef("pk"), role=Role.ADMIN).order_by("-created_at", "-pk")
    return (
        User.objects.filter(role=Role.ADMIN)
        .select_related("organization")
        .annotate(membership_status=Subquery(latest.values("status")[:1]))
        .annotate(
            admin_status=Case(
                When(membership_status=MembershipStatus.PENDING, then=Value("pending")),
                When(is_active=False, then=Value("inactive")),
                When(membership_status=MembershipStatus.ENDED, then=Value("inactive")),
                default=Value("active"),
                output_field=CharField(),
            )
        )
    )


REFUSED_MESSAGE = "This email cannot be invited to this organization. Ask the Super Admin for help."
OPEN_STATUSES = (MembershipStatus.PENDING, MembershipStatus.ACTIVE)


def open_invitation(actor, organization, email, full_name, role):
    """Create the pending invitation and its first activation code, without sending anything.

    Each organization has its own account for an email. An email with no account in this organization gets
    a new account that cannot sign in until activation, even when the same email has an account in another
    organization (that account, its password and its data are not touched). An email whose account in this
    organization ended earlier gets only a new pending membership, and its password is not changed.
    Returns (user, membership, code). Raises ValueError when the invitation is refused.
    The caller emails the code with send_activation_code once its own transaction has committed.
    """
    email = email.strip().lower()
    role = Role(role)
    if role == Role.SUPER_ADMIN:
        raise ValueError(REFUSED_MESSAGE)
    with transaction.atomic():
        # The Super Admin's email is global and can never be used for an organization account.
        if User.objects.filter(email=email, role=Role.SUPER_ADMIN).exists():
            raise ValueError(REFUSED_MESSAGE)
        user = User.objects.select_for_update().filter(email=email, organization=organization).first()
        if user is None:
            user = User(email=email, full_name=full_name)
            user.set_unusable_password()
        elif user.memberships.filter(status__in=OPEN_STATUSES).exists():
            raise ValueError("This person already has an open invitation or membership in your organization.")
        user.organization = organization
        user.role = role
        user.is_active = False  # the account opens only when the invitation is activated
        user.save()
        membership = Membership.objects.create(
            user=user, organization=organization, role=role, status=MembershipStatus.PENDING, created_by=actor
        )
        code = issue_code(membership)
        record_event(actor, "membership.invited", organization=organization, target=user, detail=f"role {role.value}")
    return user, membership, code


def create_organization_admin(actor, organization, email, full_name, request):
    """Invite an Admin for an organization and email the activation code. Returns (user, email_sent)."""
    user, membership, code = open_invitation(actor, organization, email, full_name, Role.ADMIN)
    return user, send_activation_code(request, membership, code)


def pending_membership(user):
    """The invitation a person still has to activate, or None."""
    return user.memberships.filter(status=MembershipStatus.PENDING).select_related("organization").first()


def active_membership(user, organization):
    """The person's active membership in `organization`, or None."""
    return user.memberships.filter(organization=organization, status=MembershipStatus.ACTIVE).first()


def open_membership_in(user, organization):
    """The person's pending or active membership in `organization`, or None."""
    return user.memberships.filter(organization=organization, status__in=OPEN_STATUSES).first()


def awaiting_role_membership(user):
    """The membership a transferred person waits in until the destination Admin gives them a role, or None."""
    return (
        user.memberships.filter(status=MembershipStatus.PENDING, role="").select_related("organization").first()
    )


def end_membership(membership, reason):
    """Close an open membership and keep it as history. The account is turned off until a new membership is active.

    Unused activation codes of the membership stop working, and a waiting transfer from it is cancelled.
    Call inside a transaction, together with the clean-up and audit of the change.
    """
    membership.status = MembershipStatus.ENDED
    membership.end_reason = reason
    membership.ended_at = timezone.now()
    membership.save(update_fields=["status", "end_reason", "ended_at"])
    ActivationCode.objects.filter(membership=membership, used_at__isnull=True).delete()
    Transfer.objects.filter(source_membership=membership, status=TransferStatus.AWAITING_CODE).update(
        status=TransferStatus.CANCELLED
    )
    user = membership.user
    user.is_active = False
    user.save(update_fields=["is_active"])


ASSIGNABLE_ROLES = (Role.EMPLOYEE, Role.MANAGER)


def assign_role(actor, membership, role):
    """Destination Admin: give a transferred person their role in the organization.

    The membership becomes active and the person signs in with their existing password.
    Raises ValueError when the membership is not waiting for a role in the Admin's organization.
    """
    if role not in ASSIGNABLE_ROLES:
        raise ValueError("Choose Employee or Manager.")
    role = Role(role)
    waiting = membership.status == MembershipStatus.PENDING and membership.role == ""
    if actor.role != Role.ADMIN or membership.organization_id != actor.organization_id or not waiting:
        raise ValueError("This person is not waiting for a role.")
    with transaction.atomic():
        membership.role = role
        membership.status = MembershipStatus.ACTIVE
        membership.activated_at = timezone.now()
        membership.save(update_fields=["role", "status", "activated_at"])
        user = membership.user
        user.organization = membership.organization
        user.role = role
        user.is_active = True
        user.save(update_fields=["organization", "role", "is_active"])
        record_event(
            actor, "membership.role_assigned", organization=membership.organization, target=user, detail=f"role {role.value}"
        )
    return membership


def too_many_code_attempts(request, prefix, email):
    """Count one code attempt for this email and client. True when either has tried too often this hour."""
    email_key = key_for(f"{prefix}-email", email)
    ip_key = key_for(f"{prefix}-ip", client_ip(request))
    if too_many(email_key, ACTIVATE_LIMIT_PER_EMAIL) or too_many(ip_key, ACTIVATE_LIMIT_PER_IP):
        return True
    hit(email_key, ACTIVATE_WINDOW_SECONDS)
    hit(ip_key, ACTIVATE_WINDOW_SECONDS)
    return False


def send_activation_code(request, membership, code):
    """Email the activation code. The email holds no password and no secret in its link. Returns True if sent."""
    user = membership.user
    context = {
        "code": code,
        "email": user.email,
        "role_label": membership.get_role_display(),
        "organization_name": membership.organization.name,
        "activation_url": request.build_absolute_uri(reverse("activate")) + "?" + urlencode({"invite": invite_token(user)}),
        "expiry_hours": int(CODE_LIFETIME.total_seconds() // 3600),
    }
    subject = render_to_string("accounts/activation_subject.txt", context).strip()
    body = render_to_string("accounts/activation_email.txt", context)
    try:
        send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [user.email])
    except Exception:  # Mail problems must not undo the invitation; the Admin can resend.
        # The traceback shows the exact error. Brevo's status and safe error text are logged by config/brevo_email.py.
        # The address, the code and the API key are never put into this message.
        logger.exception("activation email failed for membership %s", membership.pk)
        return False
    return True


def change_own_name(user, form):
    """Save the signed-in person's new full name. The audit entry holds the old and new names, and is
    written only when the name really changed. Email, role and organization are never changed here."""
    old_name = form.initial["full_name"]  # the name before the form changed the instance
    with transaction.atomic():
        user = form.save()
        if user.full_name != old_name:
            # The audit note holds at most 300 characters, so each name is shortened to fit.
            detail = f"from {old_name[:120]!r} to {user.full_name[:120]!r}"
            record_event(user, "user.name_changed", organization=user.organization, target=user, detail=detail[:300])
    return user


def change_own_password(user, form):
    """Save the password from a PasswordChangeForm and audit it. The audit entry never holds the password."""
    with transaction.atomic():
        user = form.save()
        record_event(user, "user.password_changed", organization=user.organization, target=user)
    return user


def set_user_active(actor, user, active):
    """Activate or deactivate an account. Does nothing if nothing changes."""
    if user.is_active == active:
        return
    with transaction.atomic():
        user.is_active = active
        user.save(update_fields=["is_active"])
        action = "user.activated" if active else "user.deactivated"
        record_event(actor, action, organization=user.organization, target=user)


def can_resend_invite(viewer, membership):
    """Only a pending invitation can get a new code, and only from the person who may create that membership.

    The Super Admin resends Admin invitations. An Admin resends invitations for Employees and Managers in their own organization.
    """
    if membership.status != MembershipStatus.PENDING:
        return False
    if viewer.role == Role.SUPER_ADMIN:
        return membership.role == Role.ADMIN
    if viewer.role == Role.ADMIN:
        return membership.organization_id == viewer.organization_id and membership.role in (Role.EMPLOYEE, Role.MANAGER)
    return False


def resend_invite(actor, user, request):
    """Send a new activation code for a pending invitation. Returns True if the email was sent.

    Raises ValueError when there is nothing to resend, the resend is not allowed, or too many
    codes were asked for. The new code replaces the unused one, so the old code stops working.
    """
    membership = pending_membership(user)
    if membership is None or not can_resend_invite(actor, membership):
        raise ValueError("This invitation cannot be resent.")
    key = key_for("invite-resend", str(user.pk))
    if too_many(key, RESEND_INVITE_LIMIT):
        raise ValueError("Too many codes were sent for this invitation. Try again later.")
    hit(key, RESEND_INVITE_WINDOW_SECONDS)
    code = issue_code(membership)
    sent = send_activation_code(request, membership, code)
    if sent:
        record_event(actor, "membership.invite_resent", organization=membership.organization, target=user)
    return sent


ACTIVATE_LIMIT_PER_EMAIL = 20  # attempts per email address per hour
ACTIVATE_LIMIT_PER_IP = 60  # attempts per client address per hour
ACTIVATE_WINDOW_SECONDS = 60 * 60
LIMITED_MESSAGE = "Too many attempts. Try again later."
INVALID_MESSAGE = "The code is not valid. Check the email address and code, or ask your administrator for a new code."
EXPIRED_MESSAGE = "This code has expired. Ask your administrator for a new code."
USED_MESSAGE = "This code has already been used. Sign in with your email and password."
LOCKED_MESSAGE = "This code is locked after too many wrong tries. Ask your administrator for a new code."
PASSWORD_NEEDED_MESSAGE = "Choose a password to finish activation."


def activate_membership(request, email, code, password=""):
    """Activate a pending invitation with its code. Returns (user, error_message); the message is "" on success.

    A new identity sets its password here. An existing identity keeps its password and only gets
    the new membership. Activation never turns on monitoring or registers a device.
    """
    email = email.strip().lower()
    if too_many_code_attempts(request, "activate", email):
        return None, LIMITED_MESSAGE

    # The same email may have a pending invitation in several organizations. The code says which one it is.
    pending = list(
        Membership.objects.filter(user__email=email, status=MembershipStatus.PENDING)
        .select_related("user", "organization")
        .order_by("created_at", "pk")
    )
    if not pending:
        if ActivationCode.objects.filter(membership__user__email=email, used_at__isnull=False).exists():
            return None, USED_MESSAGE
        return None, INVALID_MESSAGE

    membership, result, tries_left = check_code_for_any(pending, code)
    if result == WRONG:
        return None, f"The code is not correct. Tries left before it locks: {tries_left}."
    if result == LOCKED:
        return None, LOCKED_MESSAGE
    if result == EXPIRED:
        return None, EXPIRED_MESSAGE
    if result != OK:
        return None, INVALID_MESSAGE

    user = membership.user  # the account of the organization this code belongs to
    new_identity = not user.has_usable_password()
    if new_identity:
        if not password:
            return None, PASSWORD_NEEDED_MESSAGE
        try:
            validate_password(password, user)
        except ValidationError as error:
            return None, " ".join(error.messages)

    with transaction.atomic():
        if not mark_used(membership):  # a concurrent request used the code first
            return None, USED_MESSAGE
        if new_identity:
            user.set_password(password)
        user.is_active = True
        user.organization = membership.organization
        user.role = membership.role
        user.save()
        membership.status = MembershipStatus.ACTIVE
        membership.activated_at = timezone.now()
        membership.save(update_fields=["status", "activated_at"])
        record_event(user, "user.activated", organization=membership.organization, target=user)
    return user, ""
