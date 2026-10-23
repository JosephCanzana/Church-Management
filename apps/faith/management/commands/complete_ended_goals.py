"""complete_ended_goals: nightly job that completes goals whose end date has passed.

Run by production/deploy/scheduler.py (nightly, Manila time). In development run it by hand:
docker compose exec web python manage.py complete_ended_goals
`--dry-run` only prints how many goals would complete and changes nothing. Safe to run twice.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.faith import jobs
from apps.faith.models import Goal


class Command(BaseCommand):
    """Complete active goals whose end date is before today (Manila)."""

    help = "Mark active goals whose end date has passed as completed. Safe to run twice."

    def add_arguments(self, parser):
        """Add the --dry-run switch."""
        parser.add_argument("--dry-run", action="store_true", help="Count what would complete; change nothing.")

    def handle(self, *args, **options):
        """Complete (or just count) the ended goals and print the number."""
        if options["dry_run"]:
            count = Goal.objects.filter(status=Goal.Status.ACTIVE, end_date__lt=timezone.localdate()).count()
            self.stdout.write(f"ended goals: {count} would complete (dry run, nothing changed)")
            return
        self.stdout.write(f"ended goals completed: {jobs.complete_ended_goals()}")
