"""dashboards.services: everything a dashboard page shows.

``get_dashboard_context()`` is the only entry point. The views stay thin and hand
the result to a template. Pieces:

* shared by every role: greeting name, verse of the day (in the reader's Bible
  version), "continue reading" (the person's Bible bookmark), quick links (taken from ``NAV_ITEMS``, so a menu
  change is picked up here too) and the "coming soon" cards;
* leaders only (super-admin, admin, coordinator): counts and charts for what they
  manage. Attendance and tithes numbers come from *uploaded* sheets (tithes: finalized
  reports) and use the frozen values stored on them, never a recount.

Charts are drawn by the templates from plain ``Bar`` rows (label, text, percent),
so there is no JavaScript and the colors follow the theme tokens.

Privacy: tithe amounts are shown only to leaders, only here, and are never written
to ``audit_log`` or ``notification.data``.
"""
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.urls import NoReverseMatch, reverse

from apps.accounts.models import Extension, Role, Status, User
from apps.accounts.navigation import build_nav
from apps.attendance.models import Attendance
from apps.bible import daily_verse
from apps.bible import services as bible_services
from apps.bible.books import BOOKS_BY_ID
from apps.core.text import title_case
from apps.tithes.models import TithesOffering

# Name of the cookie the Bible reader uses for the chosen version (apps/bible).
# It must match the reader; the value is validated by resolve_version().
VERSION_COOKIE = "bible_version"

LEADER_ROLES = (Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR)

TREND_SHEETS = 8             # how many recent gathering dates the trend charts show
EXTENSION_BAR_LIMIT = 10     # extensions listed in the "people per extension" chart
MIN_BAR_PERCENT = 4          # a non-zero value never draws an invisible bar
CURRENCY = "₱"          # peso sign; amounts are Decimal(12,2)
NAV_LABELS_NOT_QUICK_LINKS = {"Home", "Django admin"}

# Cards for features that are planned but not built yet (shown as "Coming soon").
COMING_SOON = (
    {"label": "Bible streak", "icon": "book-open", "text": "Your daily reading streak will appear here."},
    {"label": "Faith goals", "icon": "flag", "text": "Your goals and check-ins will appear here."},
    {"label": "Upcoming events", "icon": "calendar-days", "text": "The next gatherings will appear here."},
)


@dataclass(frozen=True)
class Bar:
    """One bar of a chart: text for the label and value, and how tall it is (0-100)."""

    label: str
    display: str   # full value as text, e.g. "12" or "₱1,500.00"
    short: str     # compact value for narrow column charts, e.g. "1.5k"
    pct: int


def get_dashboard_context(user, version_code, path):
    """Return the ``dashboard`` dict a role template renders.

    ``version_code`` is the Bible version cookie (or None) and ``path`` is the
    current URL path, used to mark quick links. ``leader`` is None for members.
    """
    return {
        "greeting_name": title_case(user.first_name),
        "verse": _verse_of_the_day(version_code),
        "continue_reading": _continue_reading(user),
        "bible_url": _safe_reverse("bible:reader"),
        "start_url": _bible_chapter_url("Gen", 1),
        "quick_links": _quick_links(user, path),
        "coming_soon": COMING_SOON,
        "leader": _leader_overview(user) if user.role in LEADER_ROLES else None,
    }


# --------------------------------------------------------------- scoping
# AGENTS.md says coordinator-scoped data goes through the project's for_user(user)
# queryset helper. These three functions are the only places this app narrows
# data to a coordinator's extension. Attendance and tithes go through their own
# `for_user`; only people still filter by hand.
def _scope_people(queryset, user):
    """Limit people to the coordinator's own extension; leaders above see everyone."""
    if user.role == Role.COORDINATOR:
        return queryset.filter(extension_id=user.extension_id)
    return queryset


def _scope_attendance(user):
    """Uploaded sheets the person may see, through `Attendance.objects.for_user`.

    Drafts are never part of a dashboard, so only uploaded sheets are returned (a reopened
    sheet is a draft again and drops out until it is uploaded once more).
    """
    return Attendance.objects.for_user(user).filter(status=Attendance.Status.UPLOADED)


def _scope_tithes(user):
    """FINALIZED reports the person may see, through `TithesOffering.objects.for_user`.

    Same shape as `_scope_attendance`: drafts are never part of a dashboard, and a report counts only while
    its sheet is uploaded (a reopened report is a draft again, an archived or reopened sheet drops out).
    A coordinator gets their own extension only; `for_user` is the single place that decides.
    """
    return TithesOffering.objects.for_user(user).filter(
        status=TithesOffering.Status.FINALIZED,
        attendance__status=Attendance.Status.UPLOADED,
    )


# ----------------------------------------------------------- shared pieces
def _safe_reverse(name, *args):
    """Return the URL for a route name, or "" when the route does not exist yet."""
    try:
        return reverse(name, args=args)
    except NoReverseMatch:
        return ""


def _bible_chapter_url(osis_id, chapter):
    """Return the reader URL for a chapter, or "" if the Bible routes cannot be resolved.

    Tries the named route first; otherwise builds the path under the reader's URL
    (``/bible/<book>/<chapter>/``, see the Bible section of AGENTS.md).
    """
    url = _safe_reverse("bible:chapter", osis_id, chapter)
    if url:
        return url
    base = _safe_reverse("bible:reader")
    return f"{base}{osis_id}/{chapter}/" if base else ""


def _verse_of_the_day(version_code):
    """Return today's verse in the reader's version (text, or a note when it cannot be read)."""
    version = bible_services.resolve_version(version_code)
    row = daily_verse.get_daily_verse()
    verse = daily_verse.get_daily_verse_text(row, version)
    return {
        "reference": verse.reference,
        "version": verse.version,
        "text": verse.text,
        "note": verse.note,
        "available": verse.available,
        "chapter_url": _bible_chapter_url(row.book, row.chapter),
    }


def _continue_reading(user):
    """Return the person's bookmark as ``{"label", "url"}``, or None when they have none.

    The bookmark is the only source (the reader moves it on Next and after 20 seconds on a
    chapter). With no bookmark, or one that points at a book that no longer exists, the
    template offers "Start reading" at Genesis 1 instead (``start_url`` in the context).
    """
    bookmark = bible_services.get_bookmark(user)
    book = BOOKS_BY_ID.get(bookmark.book) if bookmark else None
    if book is None:
        return None
    return {
        "label": f"{book.name} {bookmark.chapter}",
        "url": _bible_chapter_url(bookmark.book, bookmark.chapter),
    }


def _quick_links(user, path):
    """Return the person's menu entries (from NAV_ITEMS) as shortcut cards, minus Home and Django admin."""
    nav = build_nav(user.role, path)
    return [
        entry
        for section in nav["sections"]
        for entry in section["items"]
        if entry["label"] not in NAV_LABELS_NOT_QUICK_LINKS
    ]


# ------------------------------------------------------------ leader pieces
def _leader_overview(user):
    """Return the counts and chart rows for a super-admin, admin or coordinator."""
    people = _scope_people(User.objects.all(), user)
    if user.role != Role.SUPER_ADMIN:
        people = people.exclude(role=Role.SUPER_ADMIN)  # only the super-admin sees super-admin rows

    role_counts = dict(
        people.exclude(status=Status.ARCHIVED).values_list("role").annotate(n=Count("id")).order_by()
    )
    status_counts = dict(people.values_list("status").annotate(n=Count("id")).order_by())

    attendance_rows = _attendance_trend(user)
    dates = [row["attendance_date"] for row in attendance_rows]
    tithe_amounts = _tithe_totals(user, dates)

    is_coordinator = user.role == Role.COORDINATOR
    extension_total = 0 if is_coordinator else Extension.objects.filter(archived_at__isnull=True).count()
    extension_bars = [] if is_coordinator else _people_per_extension()

    return {
        "scope_label": _scope_label(user),
        "tiles": _tiles(status_counts, extension_total, attendance_rows, is_coordinator),
        "role_bars": _bars([(label, role_counts[value]) for value, label in Role.choices if role_counts.get(value)]),
        "status_bars": _bars(
            [(label, status_counts[value]) for value, label in Status.choices if status_counts.get(value)]
        ),
        "show_extensions": not is_coordinator,
        "extension_bars": extension_bars,
        "extension_more": max(0, extension_total - len(extension_bars)),
        "attendance_bars": _bars([(_day_label(r["attendance_date"]), r["present"]) for r in attendance_rows]),
        "first_timer_bars": _bars([(_day_label(r["attendance_date"]), r["first"]) for r in attendance_rows]),
        "tithe_bars": _bars(
            [(_day_label(day), tithe_amounts.get(day, {}).get("tithes", Decimal("0"))) for day in dates],
            display=_peso,
            short=_compact,
        ),
        "offering_bars": _bars(
            [(_day_label(day), tithe_amounts.get(day, {}).get("offering", Decimal("0"))) for day in dates],
            display=_peso,
            short=_compact,
        ),
    }


def _scope_label(user):
    """Say whose numbers these are: one extension for a coordinator, otherwise everyone."""
    if user.role == Role.COORDINATOR and user.extension_id:
        return title_case(user.extension.name)
    return "All extensions"


def _tiles(status_counts, extension_total, attendance_rows, is_coordinator):
    """Return the small headline numbers at the top of a leader dashboard."""
    tiles = [{"label": "Active people", "value": status_counts.get(Status.ACTIVE.value, 0), "hint": "Activated accounts"}]
    if not is_coordinator:
        tiles.append({"label": "Extensions", "value": extension_total, "hint": "Not archived"})
    if attendance_rows:
        latest = attendance_rows[-1]
        tiles.append(
            {"label": "Latest attendance", "value": latest["present"], "hint": _day_label(latest["attendance_date"])}
        )
    else:
        tiles.append({"label": "Latest attendance", "value": "–", "hint": "No uploaded sheets yet"})
    return tiles


def _attendance_trend(user):
    """Return the last few gathering dates (oldest first) with frozen present and first-timer totals.

    Only uploaded sheets count (drafts are not final, archived ones are gone). Sheets
    on the same date, for example from several extensions, are added together.
    """
    sheets = _scope_attendance(user)
    rows = list(
        sheets.values("attendance_date")
        .annotate(present=Coalesce(Sum("total_present"), 0), first=Coalesce(Sum("first_timers_count"), 0))
        .order_by("-attendance_date")[:TREND_SHEETS]
    )
    rows.reverse()
    return rows


def _tithe_totals(user, dates):
    """Return {date: {"tithes", "offering"}} of FROZEN values for finalized reports on those dates.

    Never a recount: `tithes_total` and `offering_amount` are read as stored. Reports on the same date
    (several services, several extensions) are added together.
    """
    if not dates:
        return {}
    rows = (
        _scope_tithes(user).filter(attendance__attendance_date__in=dates)
        .values("attendance__attendance_date")
        .annotate(tithes=Sum("tithes_total"), offering=Sum("offering_amount")).order_by()
    )
    return {
        row["attendance__attendance_date"]: {"tithes": row["tithes"], "offering": row["offering"]}
        for row in rows
    }


def _people_per_extension():
    """Return bars of the biggest extensions by people who are not archived (super-admin and admin only)."""
    rows = (
        Extension.objects.filter(archived_at__isnull=True)
        .annotate(n=Count("members", filter=~Q(members__status=Status.ARCHIVED.value)))
        .order_by("-n", "name")[:EXTENSION_BAR_LIMIT]
    )
    return _bars([(title_case(extension.name), extension.n) for extension in rows])


# ----------------------------------------------------------------- helpers
def _bars(rows, display=str, short=None):
    """Turn (label, value) rows into ``Bar`` objects scaled against the biggest value."""
    top = max((float(value) for _label, value in rows), default=0.0)
    make_short = short or display
    bars = []
    for label, value in rows:
        pct = round(float(value) / top * 100) if top else 0
        if value and pct < MIN_BAR_PERCENT:
            pct = MIN_BAR_PERCENT
        bars.append(Bar(label=label, display=display(value), short=make_short(value), pct=pct))
    return bars


def _day_label(day):
    """Return a short date such as ``Oct 12``."""
    return f"{day:%b} {day.day}"


def _peso(value):
    """Return an amount as text, for example ``₱1,500.00``."""
    return f"{CURRENCY}{value:,.2f}"


def _compact(value):
    """Return a short number for narrow charts: 950, 1.5k, 2.3m."""
    number = float(value)
    for limit, suffix in ((1_000_000, "m"), (1_000, "k")):
        if number >= limit:
            return f"{number / limit:.1f}".rstrip("0").rstrip(".") + suffix
    return f"{number:.0f}"
