from django.core.management.base import BaseCommand

from screenshots.services import delete_expired, give_up_unfinished


class Command(BaseCommand):
    help = (
        "Give up uploads that never finished, and delete screenshots older than each organization's "
        "retention period. An organization with no retention period set loses nothing. Run it daily."
    )

    def handle(self, *args, **options):
        unfinished = give_up_unfinished()
        deleted = delete_expired()
        self.stdout.write(f"Gave up {unfinished} unfinished upload(s). Deleted {deleted} expired screenshot(s).")
