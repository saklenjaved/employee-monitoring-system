import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from analytics.summaries import rebuild_recent


class Command(BaseCommand):
    help = (
        "Rebuild the daily usage and activity totals for every employee-day that received "
        "events recently. Run it on a schedule, for example every 5 minutes, or with --every."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--since-hours",
            type=int,
            default=72,
            help="Rebuild days touched by events received in the last N hours (default 72, the agent's queue age).",
        )
        parser.add_argument(
            "--every",
            type=int,
            default=0,
            help="Keep running and rebuild every N seconds (local development; scripts/start_local.ps1 uses it). "
            "0 (default) rebuilds once and stops.",
        )

    def handle(self, *args, since_hours, every, **options):
        while True:
            try:
                count = rebuild_recent(since_hours=since_hours)
                self.stdout.write(f"Rebuilt {count} employee-day(s).")
            except Exception as error:
                if not every:
                    raise
                # One failed pass (for example a locked database) must not stop the loop. The next pass retries.
                self.stderr.write(f"build_summaries pass failed ({type(error).__name__}); retrying in {every} s.")
            if not every:
                return
            close_old_connections()
            try:
                time.sleep(every)
            except KeyboardInterrupt:
                return
