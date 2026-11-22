import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from accounts.models import Role, User


class Command(BaseCommand):
    """Create the first Super Admin from environment variables, for hosts without a shell (such as Render's free plan).

    It runs only when no Super Admin exists yet, so it never creates a duplicate. The password is read from
    the environment and is never printed. Remove INITIAL_SUPERADMIN_PASSWORD from the host after the first deploy.
    """

    help = "Create the first Super Admin from INITIAL_SUPERADMIN_EMAIL and INITIAL_SUPERADMIN_PASSWORD, if none exists."

    def handle(self, *args, **options):
        email = os.environ.get("INITIAL_SUPERADMIN_EMAIL", "").strip().lower()
        password = os.environ.get("INITIAL_SUPERADMIN_PASSWORD", "")
        if not email or not password:
            self.stdout.write("Initial Super Admin: variables not set, nothing to do.")
            return

        if User.objects.filter(role=Role.SUPER_ADMIN).exists():
            self.stdout.write("Initial Super Admin: a Super Admin already exists, nothing to do.")
            return

        # Log and skip instead of failing, so a wrong variable never stops the web service from starting.
        if User.objects.filter(email=email).exists():
            self.stdout.write("Initial Super Admin: skipped, a user with that email already exists. Change INITIAL_SUPERADMIN_EMAIL.")
            return

        try:
            validate_password(password)
        except ValidationError as error:
            self.stdout.write("Initial Super Admin: skipped, the password is too weak. " + " ".join(error.messages))
            return

        User.objects.create_superuser(
            email=email,
            password=password,
            full_name=os.environ.get("INITIAL_SUPERADMIN_NAME", "Super Admin"),
        )
        self.stdout.write(f"Initial Super Admin created: {email}")
