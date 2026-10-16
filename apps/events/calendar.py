"""events.calendar: the month grid behind the calendar screen.

Pure helpers: no request, no template, no permission rules (the view hands in events it
already narrowed with `permissions.visible_events`). Everything is read in the CURRENT time
zone (Asia/Manila), so an event at 01:00 Manila time lands on its Manila day, not the UTC one.

Weeks start on Sunday. A multi-day event appears on each of its days. An event that ends
exactly at midnight does not spill onto the next day.
"""
import calendar as _calendar
import datetime as dt
from dataclasses import dataclass, field

from django.db.models import Q
from django.utils import timezone

WEEK_STARTS_ON = 6          # calendar.SUNDAY
MAX_CHIPS = 3               # chips drawn in a day cell before "+N more"
MIN_YEAR, MAX_YEAR = 2000, 2100
WEEKDAYS = ("Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat")


@dataclass
class Day:
    """One cell of the grid, with the events that touch that day."""

    date: dt.date
    in_month: bool
    is_today: bool
    events: list = field(default_factory=list)

    @property
    def iso(self):
        return self.date.isoformat()

    @property
    def shown(self):
        """The events drawn as chips."""
        return self.events[:MAX_CHIPS]

    @property
    def more(self):
        """How many events do not fit as chips (the "+N more" number)."""
        return max(len(self.events) - MAX_CHIPS, 0)


# ----------------------------------------------------------------- months
def parse_month(value, today):
    """Read "YYYY-MM" into (year, month); anything invalid or out of range means today's month."""
    try:
        year_text, month_text = (value or "").split("-")
        year, month = int(year_text), int(month_text)
        dt.date(year, month, 1)
    except (ValueError, TypeError):
        return today.year, today.month
    if not MIN_YEAR <= year <= MAX_YEAR:
        return today.year, today.month
    return year, month


def month_param(year, month):
    """The "YYYY-MM" text used in the ?month= parameter."""
    return f"{year:04d}-{month:02d}"


def shift_month(year, month, delta):
    """The (year, month) that is `delta` months away (negative = earlier)."""
    year, month0 = divmod(year * 12 + (month - 1) + delta, 12)
    return year, month0 + 1


def month_weeks(year, month):
    """The month as whole weeks of dates (Sunday first), including the spill-over days."""
    return _calendar.Calendar(firstweekday=WEEK_STARTS_ON).monthdatescalendar(year, month)


def month_window(year, month):
    """(start, end) aware datetimes covering the whole grid, end exclusive."""
    weeks = month_weeks(year, month)
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(dt.datetime.combine(weeks[0][0], dt.time.min), tz)
    end = timezone.make_aware(
        dt.datetime.combine(weeks[-1][-1] + dt.timedelta(days=1), dt.time.min), tz,
    )
    return start, end


# ----------------------------------------------------------------- events
def events_in_window(queryset, start, end):
    """Narrow `queryset` to events that overlap [start, end). An event with no end is a point in time."""
    return queryset.filter(start_at__lt=end).filter(
        Q(end_at__isnull=True, start_at__gte=start) | Q(end_at__gt=start),
    )


def event_days(event, first, last):
    """The local dates (limited to first..last) on which `event` appears."""
    tz = timezone.get_current_timezone()
    start = timezone.localtime(event.start_at, tz).date()
    end = start
    if event.end_at is not None:
        end_local = timezone.localtime(event.end_at, tz)
        end = end_local.date()
        if end_local.time() == dt.time.min and end > start:
            end -= dt.timedelta(days=1)       # ends at midnight: the new day is not part of it
    day, stop = max(start, first), min(end, last)
    while day <= stop:
        yield day
        day += dt.timedelta(days=1)


def build_month(year, month, events, today=None):
    """The grid for one month: a list of weeks, each a list of seven `Day` objects."""
    today = today or timezone.localdate()
    weeks = [
        [Day(date=d, in_month=d.month == month, is_today=d == today) for d in week]
        for week in month_weeks(year, month)
    ]
    cells = {day.date: day for week in weeks for day in week}
    first, last = weeks[0][0].date, weeks[-1][-1].date
    for event in events:
        for d in event_days(event, first, last):
            cells[d].events.append(event)
    for cell in cells.values():
        cell.events.sort(key=lambda e: (e.start_at, e.title.lower(), e.pk or 0))
    return weeks
