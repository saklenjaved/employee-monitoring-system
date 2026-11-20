from django.core.management.base import BaseCommand, CommandError

from devices.pairing import issue_enrollment_token
from organizations.models import Organization


class Command(BaseCommand):
    """Create a new enrollment token for an organization. Shown once. A new token replaces the old one."""

    help = "Create a new enrollment token for the installer of one organization."

    def add_arguments(self, parser):
        parser.add_argument("--org", required=True, help="The organization name, exactly as stored.")

    def handle(self, *args, **options):
        try:
            organization = Organization.objects.get(name=options["org"])
        except Organization.DoesNotExist as exc:
            raise CommandError(f"No organization named {options['org']!r}.") from exc
        token = issue_enrollment_token(organization)
        self.stdout.write(f"Enrollment token for {organization.name} (shown once):")
        self.stdout.write(token)
