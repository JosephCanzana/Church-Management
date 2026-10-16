"""send_event_reminders: the 15-minute job that sends event reminders (brick 13).

Run by production/deploy/scheduler.py (every 15 minutes, Manila time). In development run it by
hand: docker compose exec web python manage.py send_event_reminders
`--dry-run` only prints how many reminders are due and sends nothing.
"""
from django.core.management.base import BaseCommand

from apps.events import services


class Command(BaseCommand):
    help = "Send the event reminders that are due (bell item + push). Safe to run twice."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Count what is due; send nothing.")

    def handle(self, *args, **options):
        if options["dry_run"]:
            due = services.due_reminders()
            people = sum(len(ids) for _, by_lead in due for ids in by_lead.values())
            self.stdout.write(f"event reminders due: {people} for {len(due)} events (dry run, nothing sent)")
            return
        result = services.send_reminders()
        self.stdout.write(f"event reminders sent: {result['reminders']} for {result['events']} events")
