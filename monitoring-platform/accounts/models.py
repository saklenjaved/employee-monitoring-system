from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.db import models
from django.db.models import Q
from django.utils import timezone


class Role(models.TextChoices):
    SUPER_ADMIN = "super_admin", "Super Admin"
    ADMIN = "admin", "Admin"
    MANAGER = "manager", "Manager"
    EMPLOYEE = "employee", "Employee"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("An email address is required.")
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        # A Django superuser is the platform Super Admin. It belongs to no organization.
        extra_fields.setdefault("role", Role.SUPER_ADMIN)
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        # A Super Admin's email is global: no other account in any organization may use it.
        if self.model.objects.filter(email=(email or "").strip().lower()).exists():
            raise ValueError("An account with this email already exists.")
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """A person's account in one organization. Login uses the email address.

    The same email may have a separate account (own password and status) in each organization, but only one
    in the same organization. The Super Admin has no organization, and that email is unique across the platform.
    """

    email = models.EmailField()
    full_name = models.CharField(max_length=200)
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.EMPLOYEE)
    # Empty only for the Super Admin. Everyone else belongs to exactly one organization.
    organization = models.ForeignKey(
        "organizations.Organization",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="users",
    )
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)  # Django admin site access only
    date_joined = models.DateTimeField(default=timezone.now)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        ordering = ["email"]
        constraints = [
            # One account per email in an organization. The same email can exist in other organizations.
            models.UniqueConstraint(fields=["email", "organization"], name="unique_email_per_organization"),
            # The Super Admin has no organization, so the rule above does not cover it: its email is global.
            models.UniqueConstraint(fields=["email"], condition=Q(role="super_admin"), name="unique_super_admin_email"),
            # Super Admin has no organization; every other role must have one.
            models.CheckConstraint(
                condition=(
                    Q(role="super_admin", organization__isnull=True)
                    | (~Q(role="super_admin") & Q(organization__isnull=False))
                ),
                name="user_organization_matches_role",
            ),
        ]

    def save(self, *args, **kwargs):
        # Store emails in lower case so "A@x.com" and "a@x.com" are the same login.
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.email


class MembershipStatus(models.TextChoices):
    PENDING = "pending", "Pending activation"
    ACTIVE = "active", "Active"
    ENDED = "ended", "Ended"


class EndReason(models.TextChoices):
    REMOVED = "removed", "Removed"
    TRANSFERRED = "transferred", "Transferred"


class Membership(models.Model):
    """One employment stint of a person in one organization.

    A person has at most one open membership (pending or active) at a time. Ended memberships
    stay as history. The organization-specific role lives here, not on the User.
    """

    user = models.ForeignKey(
        "accounts.User", on_delete=models.PROTECT, related_name="memberships"
    )
    organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT, related_name="memberships"
    )
    # Blank only while a transferred person waits for the destination Admin to assign a role.
    role = models.CharField(max_length=20, choices=[c for c in Role.choices if c[0] != Role.SUPER_ADMIN], blank=True, default="")
    status = models.CharField(max_length=10, choices=MembershipStatus.choices, default=MembershipStatus.PENDING)
    end_reason = models.CharField(max_length=12, choices=EndReason.choices, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    activated_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(status__in=["pending", "active"]),
                name="one_open_membership_per_user",
            ),
            models.CheckConstraint(
                condition=~Q(status="active") | ~Q(role=""),
                name="active_membership_has_role",
            ),
            models.CheckConstraint(
                condition=(Q(status="ended") & ~Q(end_reason="")) | (~Q(status="ended") & Q(end_reason="")),
                name="end_reason_only_when_ended",
            ),
        ]

    def __str__(self):
        return f"{self.user} @ {self.organization} ({self.status})"


class ActivationCode(models.Model):
    """A one-time 8-digit code that proves the person owns the email address.

    Only a keyed hash is stored (see accounts.services). A code works once, expires after 24 hours
    and is locked after five wrong attempts. A new code replaces the unused one.
    """

    membership = models.ForeignKey(Membership, on_delete=models.CASCADE, related_name="activation_codes")
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["membership"],
                condition=Q(used_at__isnull=True),
                name="one_unused_code_per_membership",
            ),
        ]


class TransferStatus(models.TextChoices):
    AWAITING_CODE = "awaiting_code", "Waiting for the person's code"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class Transfer(models.Model):
    """A Super Admin's request to move a person from one organization to another.

    The person confirms it with an 8-digit code sent to their email. That code is an
    ActivationCode of the source membership. Only after the code is verified does the source
    membership end and a new pending membership open in the destination, where the destination
    Admin assigns the role. The User account, email and password never change.
    """

    user = models.ForeignKey("accounts.User", on_delete=models.PROTECT, related_name="transfers")
    source_membership = models.ForeignKey(Membership, on_delete=models.PROTECT, related_name="transfers_out")
    destination_organization = models.ForeignKey(
        "organizations.Organization", on_delete=models.PROTECT, related_name="+"
    )
    # Set when the code is verified and the destination membership is created.
    destination_membership = models.ForeignKey(
        Membership, null=True, blank=True, on_delete=models.PROTECT, related_name="transfers_in"
    )
    requested_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    status = models.CharField(max_length=16, choices=TransferStatus.choices, default=TransferStatus.AWAITING_CODE)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(status="awaiting_code"),
                name="one_open_transfer_per_user",
            ),
        ]

    def __str__(self):
        return f"{self.user} to {self.destination_organization} ({self.status})"


class MonitoringPin(models.Model):
    """An employee's 6-digit PIN for starting monitoring on their PC. Only a salted hash is stored."""

    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="monitoring_pin")
    organization = models.ForeignKey("organizations.Organization", on_delete=models.PROTECT, related_name="+")
    pin_hash = models.CharField(max_length=128)
    failed_attempts = models.PositiveSmallIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)
