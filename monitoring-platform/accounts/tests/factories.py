"""Small helpers that create test data."""
from accounts.models import Membership, MembershipStatus, User
from organizations.models import Organization

PASSWORD = "a-long-test-password-1"


def make_org(name):
    return Organization.objects.create(name=name)


def make_membership(user):
    """Give a user an active membership in their own organization, as activation does in production."""
    return Membership.objects.create(
        user=user, organization=user.organization, role=user.role, status=MembershipStatus.ACTIVE
    )


def make_user(email, role, organization, full_name=None):
    user = User.objects.create_user(
        email=email, password=PASSWORD, full_name=full_name or email, role=role, organization=organization
    )
    make_membership(user)
    return user


def make_super_admin(email="super@platform.test"):
    return User.objects.create_superuser(email=email, password=PASSWORD, full_name="Super")
