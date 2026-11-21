from django.core.management.base import BaseCommand

from screenshots.storage import get_storage


class Command(BaseCommand):
    help = "Create the private screenshot bucket if it does not exist, and block all public access to it."

    def handle(self, *args, **options):
        get_storage().ensure_bucket()
        self.stdout.write("Screenshot bucket is ready.")
