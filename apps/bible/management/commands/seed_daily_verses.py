"""seed_daily_verses: save the verse of the day for the next few days ahead of time.

Not needed for the dashboard (it saves today's verse on first view), but handy to
review what is coming. Safe to run again: days that already have a row are left alone.
"""
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.bible.daily_verse import get_daily_verse, reference_label
from apps.bible.models import DailyVerse


class Command(BaseCommand):
    """Create missing ``daily_verse`` rows, starting today (Manila)."""

    help = "Save the verse of the day for the next N days (default 60). Existing days are kept."

    def add_arguments(self, parser):
        """Define --days."""
        parser.add_argument("--days", type=int, default=60, help="How many days ahead, starting today.")

    def handle(self, *args, **options):
        """Walk the days, create what is missing and print a summary."""
        days = max(1, options["days"])
        today = timezone.localdate()
        existing = set(
            DailyVerse.objects.filter(verse_date__gte=today, verse_date__lt=today + timedelta(days=days))
            .values_list("verse_date", flat=True)
        )
        created = 0
        for offset in range(days):
            day = today + timedelta(days=offset)
            row = get_daily_verse(day)
            if day not in existing:
                created += 1
            if options["verbosity"] >= 2:
                self.stdout.write(f"{day}  {reference_label(row)}")
        self.stdout.write(self.style.SUCCESS(f"{created} new day(s) saved, {days - created} already existed."))
