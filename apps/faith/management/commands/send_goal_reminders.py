"""send_goal_reminders: the 5-minute job that sends goal reminders (bell item + device push).

Run by production/deploy/scheduler.py (every 5 minutes, Manila time). In development run it by hand:
docker compose exec web python manage.py send_goal_reminders
`--dry-run` only prints how many reminders are due and sends nothing. Safe to run twice: the
per-goal, per-day dedupe key keeps a repeat quiet. Does nothing while the super-admin switch is off.
"""
from django.core.management.base import BaseCommand

from apps.core import job_settings
from apps.faith import jobs


class Command(BaseCommand):
    """Send the goal reminders that are due right now."""

    help = "Send goal reminders for timed goals that just started and are not done. Safe to run twice."

    def add_arguments(self, parser):
        """Add the --dry-run switch."""
        parser.add_argument("--dry-run", action="store_true", help="Count what is due; send nothing.")

    def handle(self, *args, **options):
        """Send (or just count) the due reminders and print the number."""
        if not job_settings.goal_reminders_enabled():
            self.stdout.write("goal reminders are switched off in Site settings; nothing sent")
            return
        if options["dry_run"]:
            self.stdout.write(f"goal reminders due: {len(jobs.due_reminder_goals())} (dry run, nothing sent)")
            return
        self.stdout.write(f"goal reminders sent: {jobs.send_goal_reminders()}")
