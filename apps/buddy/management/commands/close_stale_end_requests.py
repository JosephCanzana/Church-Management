"""close_stale_end_requests: nightly job that ends partnerships whose end request went unanswered.

The wait is the `buddy_end_request_days` site setting (default 7). Run by the scheduler every
night (Brick 10 adds it to the schedule). In development run it by hand:
docker compose exec web python manage.py close_stale_end_requests
`--dry-run` only prints how many would close and changes nothing.
"""
from django.core.management.base import BaseCommand

from apps.buddy import services


class Command(BaseCommand):
    """Close end requests that waited longer than the setting. Safe to run twice."""

    help = "End buddy partnerships whose end request was not answered in time. Safe to run twice."

    def add_arguments(self, parser):
        """Add the --dry-run switch."""
        parser.add_argument("--dry-run", action="store_true", help="Count what would close; change nothing.")

    def handle(self, *args, **options):
        """Close (or just count) the stale end requests and print the number."""
        if options["dry_run"]:
            count = len(services.stale_end_request_ids())
            self.stdout.write(f"stale end requests: {count} would close (dry run, nothing changed)")
            return
        self.stdout.write(f"stale end requests closed: {services.close_stale_end_requests()}")
