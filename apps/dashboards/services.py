"""dashboards.services: everything a dashboard page shows.

``get_dashboard_context()`` is the only entry point. The views stay thin and hand
the result to a template. Pieces:

* shared by every role: greeting name, verse of the day (in the reader's Bible
  version), "continue reading" (the person's Bible bookmark), quick links (taken from ``NAV_ITEMS``, so a menu
  change is picked up here too), the faith widgets (streaks and today's goals: read-only
  numbers from ``apps.faith.overview``) and the "coming soon" cards;
* leaders only (super-admin, admin, coordinator): counts and charts for what they
  manage. Attendance and tithes numbers come from *uploaded* sheets (tithes: finalized
  reports) and use the frozen values stored on them, never a recount.

Charts are drawn by the templates from plain ``Bar`` rows (label, text, percent),
so there is no JavaScript and the colors follow the theme tokens.

Brick 1 of the dashboard revamp adds the *data layer* next to the old pieces (nothing old was removed):
``resolve_extension`` (the admin's ``?extension=`` filter) and the stats functions ``attendance_stats``,
``people_stats``, ``extension_stats``, ``giving_stats``, ``prayer_summary`` and ``upcoming_events``. Each returns
plain dicts and lists (no templates, no chart geometry: that is ``charts.py``). Scoping still happens ONLY in
``_scope_people``, ``_scope_attendance`` and ``_scope_tithes``; prayer and events keep their own single scoping
helpers (``inbox_for`` and ``visible_events``).

Brick 2 adds what the leader pages draw: ``tiles`` (four headline numbers), ``charts`` (every chart's geometry from
``charts.py`` plus its text, ready for the ``_chart_*`` partials) and ``events_url``. They are built from the Brick 1
stats, so they follow the same scoping and the same ``?extension=`` filter and add no query of their own.

Brick 3 adds the *personal dashboard* (``get_personal_context``), open to every role: goals today with a
completion ring, streaks, a 7-day check-in strip, the verse, continue reading, the Buddy card, "My journey" and
upcoming events. It takes no person id from the request: everything is about ``user`` only. ``get_dashboard_context``
is unchanged, so the leader pages and their tests keep working.

Brick 4 adds the *period* to the overview itself (quick links, custom dates, the giving switch and the tables under
the charts: ``parse_period``, ``_period_filter``) and the standalone A4 *print report* (``get_report_context``). There is
no separate report screen: the dashboard IS the report on screen. The print report calls the Brick 1 stats directly and
never ``get_dashboard_context`` (that one writes today's ``daily_verse`` row; a print page writes and logs nothing).

Privacy: tithe amounts are shown only to leaders, only here, and are never written
to ``audit_log`` or ``notification.data``.
"""
import calendar
import math
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from urllib.parse import urlencode

from django.core.exceptions import PermissionDenied
from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.db.models.functions import Coalesce
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from apps.accounts.models import Extension, Role, Status, User
from apps.accounts.navigation import build_nav
from apps.attendance.models import Attendance
from apps.attendance.summary import GENDER_ROW_LABELS, summary_table
from apps.bible import daily_verse
from apps.bible import services as bible_services
from apps.bible.books import BOOKS_BY_ID
from apps.buddy import services as buddy_services   # read-only: live_partnership, active_partnership, partner_card
from apps.core.text import title_case
from apps.faith import overview as faith_overview
from apps.events.permissions import visible_events
from apps.faith.permissions import can_use_goals
from apps.prayer.models import PrayerRequest, signin_filter
from apps.prayer.permissions import can_use_inbox, sender_label
from apps.tithes.models import TithesOffering

from . import charts

# Name of the cookie the Bible reader uses for the chosen version (apps/bible).
# It must match the reader; the value is validated by resolve_version().
VERSION_COOKIE = "bible_version"

LEADER_ROLES = (Role.SUPER_ADMIN, Role.ADMIN, Role.COORDINATOR)

TREND_SHEETS = 8             # how many recent gathering dates the trend charts show
EXTENSION_BAR_LIMIT = 10     # extensions listed in the "people per extension" chart
MIN_BAR_PERCENT = 4          # a non-zero value never draws an invisible bar
CURRENCY = "₱"          # peso sign; amounts are Decimal(12,2)
FILTER_ROLES = (Role.SUPER_ADMIN, Role.ADMIN)   # only these two may pick an extension with ?extension=
MAX_DB_ID = 2_147_483_647    # a bigger ?extension= number would overflow the database column, so it is "bad"
PRAYER_LATEST = 5            # prayer requests listed on the dashboard card
SILENT_DAYS = 7              # an extension with no uploaded sheet this many days back counts as "not reporting"
PRAYER_SCOPE_NOTE = "This extension's coordinator handles its prayer requests."
NAV_LABELS_NOT_QUICK_LINKS = {"Home", "Django admin"}
CHART_WIDTH = 560            # drawing width (SVG units) of the line and column charts; the SVG scales to its card
CHART_HEIGHT = 220
HBAR_WIDTH = 400             # drawing width of one horizontal-bar chart
CHECKIN_DAYS = 7             # days in the personal check-in strip
JOURNEY_SERVICES = 4         # attended services listed on the "My journey" card
GOAL_RING_RADIUS = 40        # the goals-today ring: a two-segment donut (done, still to do)
GOAL_RING_STROKE = 12
CHECKIN_SERIES = ("Opened the app", "Read the Bible")
LABEL_LIMIT = 34             # longest bar label drawn in full; longer ones end with an ellipsis (full name in the tooltip)

# Cards for features that are planned but not built yet (shown as "Coming soon").
# The Bible streak and goals cards were replaced by real widgets (_streaks.html, _goals_today.html).
COMING_SOON = (
    {"label": "Upcoming events", "icon": "calendar-days", "text": "The next gatherings will appear here."},
)


@dataclass(frozen=True)
class Bar:
    """One bar of a chart: text for the label and value, and how tall it is (0-100)."""

    label: str
    display: str   # full value as text, e.g. "12" or "₱1,500.00"
    short: str     # compact value for narrow column charts, e.g. "1.5k"
    pct: int


def get_dashboard_context(user, version_code, path, extension=None, start=None, end=None, params=None):
    """Return the ``dashboard`` dict a role template renders.

    ``version_code`` is the Bible version cookie (or None) and ``path`` is the
    current URL path, used to mark quick links. ``leader`` is None for members.
    ``streaks`` and ``goals_today`` are None for people who cannot use goals (the templates then draw nothing).

    Optional (the views do not pass them yet, so every old call behaves as before):

    * ``extension``: the raw ``?extension=`` text. Only admin and super-admin may use it; a bad value means
      "All", a coordinator always gets their own extension (see ``resolve_extension``).
    * ``start`` / ``end``: dates (Manila). With neither, the stats cover the last ``TREND_SHEETS`` gathering dates.
    * ``params``: the request's ``GET`` (untrusted text), read by leaders only for the period quick links and the
      giving switch: ``preset`` (one of ``PERIOD_PRESETS``, with ``start`` / ``end`` for ``custom``) and ``giving``
      (``0`` hides the giving numbers). No ``preset`` means the default view, the last ``TREND_SHEETS``
      gatherings; a refused one also means the default, and ``period_filter["fell_back"]`` says so. ``extension``
      stays its own argument because only the admin views may pass it.

    New keys (all present for every role; None where the role may not see the data):
    ``filter``, ``period``, ``attendance_stats``, ``people_stats``, ``extension_stats``, ``giving_stats``,
    ``prayer_summary`` and ``upcoming_events`` (a list, for every role).

    Brick 2 keys: ``tiles`` (list of ``{"label", "value", "hint"}``), ``charts`` (see ``leader_charts``; both None
    for a member) and ``events_url`` (the calendar page, "" if no events route resolves). The old ``leader``
    block is unchanged and still ignores the filter; the leader pages no longer draw it.

    Brick 4 keys (None for a member): ``period_filter`` (quick links, the period form and the Print report
    querystring, see ``_period_filter``), ``network_table`` and ``attendance_gap`` (the tables under the charts) and
    ``links`` (where each card leads, see ``card_links``; tiles and charts carry their own ``url`` too).
    """
    today = timezone.localdate()
    is_leader = user.role in LEADER_ROLES
    chosen = resolve_extension(user, extension) if is_leader else None
    extension_id = chosen.pk if chosen else None

    # The period quick links (leaders only): a valid preset replaces start and end; no preset or a refused one keeps
    # the default view (the last TREND_SHEETS gatherings).
    params = params if (is_leader and params is not None) else {}
    raw_preset = str(params.get("preset") or "").strip()
    asked = parse_period(raw_preset, params.get("start"), params.get("end"), today) if raw_preset else None
    if asked is not None and not asked["fell_back"]:
        start, end = asked["start"], asked["end"]
    include_giving = _flag(params.get("giving"), default=True)

    attendance = attendance_stats(user, extension_id, start, end)
    # The period every other piece uses. By default it is exactly the dates the attendance trend shows.
    period_start, period_end = start, end
    if attendance and start is None and end is None and attendance["period"]:
        period_start = attendance["period"]["start"]
        period_end = max(attendance["period"]["end"], today)

    people = people_stats(user, extension_id, period_start, period_end)
    extensions = extension_stats(user, period_start, period_end)
    giving = giving_stats(user, extension_id, period_start, period_end) if include_giving else None
    period_given = start is not None or end is not None
    links = card_links(user)
    drawn = leader_charts(attendance, people, extensions, giving, period_given, links) if is_leader else None
    if drawn:
        drawn["attendance_line"]["label_step"] = _label_step(attendance["count"])   # a year of Sundays is thinned

    return {
        "greeting_name": title_case(user.first_name),
        "verse": _verse_of_the_day(version_code),
        "continue_reading": _continue_reading(user),
        "bible_url": _safe_reverse("bible:reader"),
        "start_url": _bible_chapter_url("Gen", 1),
        "quick_links": _quick_links(user, path),
        "streaks": _streaks(user, today),
        "goals_today": _goals_today(user, today),
        "coming_soon": COMING_SOON,
        "leader": _leader_overview(user) if is_leader else None,
        "filter": _filter_info(user, chosen),
        "period": {"start": period_start, "end": period_end} if (period_start or period_end) else None,
        "attendance_stats": attendance,
        "people_stats": people,
        "extension_stats": extensions,
        "giving_stats": giving,
        "prayer_summary": prayer_summary(user, extension_id),
        "upcoming_events": upcoming_events(user, 5),
        "events_url": _safe_reverse("events:calendar") or _safe_reverse("events:list") or _safe_reverse("events:index"),
        "tiles": dashboard_tiles(attendance, people, extensions, period_start, links) if is_leader else None,
        "links": links,
        "charts": drawn,
        "period_filter": _period_filter(user, chosen, asked, period_start, period_end, include_giving) if is_leader else None,
        "network_table": _network_table(attendance["network"]) if attendance else None,
        "attendance_gap": _attendance_gap(attendance) if attendance else None,
    }


# --------------------------------------------------------------- scoping
# AGENTS.md says coordinator-scoped data goes through the project's for_user(user)
# queryset helper. These three functions are the only places this app narrows
# data to a coordinator's extension. Attendance and tithes go through their own
# `for_user`; only people still filter by hand.
def _narrow(queryset, user, extension_id, field):
    """Narrow to one extension for admin and super-admin only; a coordinator is already narrowed by `for_user`.

    ``extension_id`` is the already validated id from ``resolve_extension`` (never raw request text).
    """
    if extension_id and user.role != Role.COORDINATOR:
        return queryset.filter(**{field: extension_id})
    return queryset


def _within(queryset, field, start, end):
    """Keep rows whose date ``field`` falls between ``start`` and ``end`` (either may be None = open)."""
    if start is not None:
        queryset = queryset.filter(**{f"{field}__gte": start})
    if end is not None:
        queryset = queryset.filter(**{f"{field}__lte": end})
    return queryset


def _scope_people(queryset, user, extension_id=None, start=None, end=None):
    """Limit people to the coordinator's own extension; leaders above see everyone.

    ``extension_id`` (admin and super-admin only; ignored for a coordinator) narrows to one extension.
    ``start`` / ``end`` keep people who joined (``created_at``, Manila date) in that period; leave them out
    for the plain headcount.
    """
    if user.role == Role.COORDINATOR:
        queryset = queryset.filter(extension_id=user.extension_id)
    else:
        queryset = _narrow(queryset, user, extension_id, "extension_id")
    return _within(queryset, "created_at__date", start, end)


def _scope_attendance(user, extension_id=None, start=None, end=None):
    """Uploaded sheets the person may see, through `Attendance.objects.for_user`.

    Drafts are never part of a dashboard, so only uploaded sheets are returned (a reopened
    sheet is a draft again and drops out until it is uploaded once more).
    ``extension_id`` (ignored for a coordinator) and ``start`` / ``end`` (on ``attendance_date``) narrow further.
    """
    sheets = Attendance.objects.for_user(user).filter(status=Attendance.Status.UPLOADED)
    sheets = _narrow(sheets, user, extension_id, "extension_id")
    return _within(sheets, "attendance_date", start, end)


def _scope_tithes(user, extension_id=None, start=None, end=None):
    """FINALIZED reports the person may see, through `TithesOffering.objects.for_user`.

    Same shape as `_scope_attendance`: drafts are never part of a dashboard, and a report counts only while
    its sheet is uploaded (a reopened report is a draft again, an archived or reopened sheet drops out).
    A coordinator gets their own extension only; `for_user` is the single place that decides.
    ``extension_id`` (ignored for a coordinator) and ``start`` / ``end`` (on the sheet's date) narrow further.
    """
    reports = TithesOffering.objects.for_user(user).filter(
        status=TithesOffering.Status.FINALIZED,
        attendance__status=Attendance.Status.UPLOADED,
    )
    reports = _narrow(reports, user, extension_id, "attendance__extension_id")
    return _within(reports, "attendance__attendance_date", start, end)


def resolve_extension(user, raw_value):
    """Turn the raw ``?extension=`` text into the extension to filter by, or None for "All".

    * admin and super-admin: the extension with that id, only if it exists and is not archived. Anything
      else (empty, letters, a negative or decimal number, a huge number, an unknown or archived id) is None.
    * coordinator: always their OWN extension, whatever the text says (nothing from the request is trusted).
    * anyone else: None.
    """
    if user.role == Role.COORDINATOR:
        return user.extension if user.extension_id else None
    if user.role not in FILTER_ROLES:
        return None
    text = str(raw_value or "").strip()
    # isascii first: str.isdigit() also accepts digits such as "²" that int() cannot read.
    if not (text.isascii() and text.isdigit()) or int(text) > MAX_DB_ID:
        return None
    return Extension.objects.filter(pk=int(text), archived_at__isnull=True).first()


AREA_NAMESPACES = {Role.SUPER_ADMIN: "superadmin", Role.ADMIN: "admin_area", Role.COORDINATOR: "coordinator_area"}


def card_links(user):
    """Return where each dashboard card leads, as ``{"people", "attendance", "giving", "prayer", "events", "extensions"}``.

    Leaders only (None for anyone else). Every value is a URL or "" when that route does not resolve or the role may
    not use it, so a card without a page simply draws no link. People, attendance sheets, tithes reports and the prayer
    inbox live in the person's OWN area (the same route names the sidebar uses); the extensions list is reached through
    the super-admin area by admin and super-admin only (a coordinator has no such page), exactly like the sidebar entry.
    Links carry no id and no querystring: they lead to the page, and the page does its own scoping.
    """
    if user.role not in LEADER_ROLES:
        return None
    area = AREA_NAMESPACES[user.role]
    return {
        "people": _safe_reverse(f"{area}:user_list"),
        "attendance": _safe_reverse(f"{area}:sheet_list"),
        "giving": _safe_reverse(f"{area}:report_list"),
        "prayer": _safe_reverse(f"{area}:prayer_inbox"),
        "events": _safe_reverse("events:calendar") or _safe_reverse("events:list") or _safe_reverse("events:index"),
        "extensions": _safe_reverse("superadmin:extension_list") if user.role in FILTER_ROLES else "",
    }


def _filter_info(user, chosen):
    """Return what the filter bar needs: whether it shows, the picked extension and the choices."""
    enabled = user.role in FILTER_ROLES
    options = []
    if enabled:
        live = Extension.objects.filter(archived_at__isnull=True).order_by("name")
        options = [{"id": ext.pk, "name": title_case(ext.name)} for ext in live]
    return {
        "enabled": enabled,
        "extension_id": chosen.pk if chosen else None,
        "label": title_case(chosen.name) if chosen else _scope_label(user),
        "options": options,
    }


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


def _streaks(user, today):
    """Return the open and Bible-reading streaks for the streak widget, or None when the person cannot use goals."""
    if not can_use_goals(user):
        return None
    return {**faith_overview.streak_cards(user, today), "url": _safe_reverse("faith:history")}


def _goals_today(user, today):
    """Return today's goal counts and next goals for the goals widget, or None when the person cannot use goals."""
    if not can_use_goals(user):
        return None
    summary = faith_overview.today_summary(user, today)
    return {**summary, **_goal_ring(summary), "url": _safe_reverse("faith:today")}


def _goal_ring(summary):
    """Return ``donut`` (geometry from ``charts.donut``), ``pct`` and ``aria`` for the goals-today ring.

    Two segments: done and still to do. Only the first is drawn as an arc; the track is the rest. With nothing
    scheduled the donut is ``empty`` and the template draws the track alone with a dash in the middle.
    """
    done, total = summary["done"], summary["total"]
    pct = round(done / total * 100) if total else 0
    aria = f"{done} of {total} goals done today ({pct} percent)." if total else "No goals scheduled today."
    donut = charts.donut(
        [("Done", done), ("To do", summary["remaining"])], radius=GOAL_RING_RADIUS, stroke=GOAL_RING_STROKE
    )
    return {"donut": donut, "pct": pct, "aria": aria}


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


# -------------------------------------------------------- stats (Brick 1)
# Plain dicts and lists, one function per dashboard card family. None of them renders anything, checks a
# request or writes to the database. Money stays Decimal. Leader-only functions return None for a member.
def _gathering_dates(user, extension_id=None, start=None, end=None):
    """Return the gathering dates to report on, oldest first.

    With ``start`` or ``end``: every date that has an uploaded sheet in that period. With neither: the last
    ``TREND_SHEETS`` dates. Several sheets on one date (other extensions, other services) are one date.
    """
    sheets = _scope_attendance(user, extension_id, start, end)
    dates = sheets.order_by("-attendance_date").values_list("attendance_date", flat=True).distinct()
    if start is None and end is None:
        dates = dates[:TREND_SHEETS]
    return sorted(dates)


def _merge_summaries(summaries):
    """Add several frozen ``Attendance.summary`` JSON values together (same shape as one summary).

    A missing summary (None) or a missing key counts as 0, like ``summary_table`` does.
    """
    merged = {}
    for summary in summaries:
        for group, genders in (summary or {}).items():
            for gender, networks in (genders or {}).items():
                cell = merged.setdefault(group, {}).setdefault(gender, {})
                for network, number in (networks or {}).items():
                    cell[network] = cell.get(network, 0) + int(number or 0)
    return merged


def _gender_by_network(summary):
    """Return men and women by age network from one (merged) summary: first timers, guests and members together.

    Shape: ``{"columns": [{"code", "label"}], "rows": [{"gender", "label", "values", "total"}], "total": n}``.
    The rows are ready for ``charts.column_groups`` (one group per column, one value per row).
    """
    table = summary_table(summary)
    values_by_gender = {}
    for group in table["groups"]:
        for row in group["rows"]:
            current = values_by_gender.get(row["gender"], [0] * len(table["columns"]))
            values_by_gender[row["gender"]] = [a + b for a, b in zip(current, row["cells"])]
    rows = [
        {"gender": gender, "label": GENDER_ROW_LABELS[gender], "values": values, "total": sum(values)}
        for gender, values in values_by_gender.items()
    ]
    return {"columns": table["columns"], "rows": rows, "total": table["total"]}


def attendance_stats(user, extension_id=None, start=None, end=None):
    """Return the attendance numbers of the uploaded sheets in scope, or None for a member.

    Everything comes from FROZEN values (``total_present``, ``first_timers_count``, ``summary``), never a
    recount, and sheets on the same date are added together. Keys:

    * ``points``: one per gathering date, oldest first: ``date``, ``label``, ``present``, ``first_timers``,
      ``men``, ``women``. ``labels`` and ``values`` repeat label and present for ``charts.line_chart``.
    * ``count``, ``total_present``, ``average`` (one decimal, 0 with no dates), ``first_timers`` (sum),
      ``highest`` and ``lowest`` (a point; the most recent wins a tie), ``latest`` (the newest point).
    * ``network``: men and women by age network over ALL dates; ``network_latest``: the same for the
      newest date only (``_gender_by_network`` shape); ``split``: ``{"men", "women"}`` over all dates.
    * ``period``: ``{"start", "end"}`` of the dates covered, or None with no uploaded sheets.
    """
    if user.role not in LEADER_ROLES:
        return None
    dates = _gathering_dates(user, extension_id, start, end)
    sheets = _scope_attendance(user, extension_id).filter(attendance_date__in=dates).values(
        "attendance_date", "total_present", "first_timers_count", "summary"
    )
    by_date = {day: {"present": 0, "first": 0, "summaries": []} for day in dates}
    for sheet in sheets:
        row = by_date[sheet["attendance_date"]]
        row["present"] += sheet["total_present"] or 0
        row["first"] += sheet["first_timers_count"] or 0
        row["summaries"].append(sheet["summary"])

    points, per_date_network = [], {}
    for day in dates:
        row = by_date[day]
        network = _gender_by_network(_merge_summaries(row["summaries"]))
        per_date_network[day] = network
        totals = {r["gender"]: r["total"] for r in network["rows"]}
        points.append({
            "date": day, "label": _day_label(day), "present": row["present"], "first_timers": row["first"],
            "men": totals.get("male", 0), "women": totals.get("female", 0),
        })

    every_summary = [s for day in dates for s in by_date[day]["summaries"]]
    total_present = sum(p["present"] for p in points)
    return {
        "points": points,
        "labels": [p["label"] for p in points],
        "values": [p["present"] for p in points],
        "count": len(points),
        "total_present": total_present,
        "average": round(total_present / len(points), 1) if points else 0,
        "first_timers": sum(p["first_timers"] for p in points),
        "highest": max(points, key=lambda p: (p["present"], p["date"]), default=None),
        "lowest": min(points, key=lambda p: (p["present"], -p["date"].toordinal()), default=None),
        "latest": points[-1] if points else None,
        "network": _gender_by_network(_merge_summaries(every_summary)),
        "network_latest": per_date_network[dates[-1]] if dates else _gender_by_network({}),
        "split": {"men": sum(p["men"] for p in points), "women": sum(p["women"] for p in points)},
        "period": {"start": dates[0], "end": dates[-1]} if dates else None,
    }


def people_stats(user, extension_id=None, start=None, end=None):
    """Return the people counts for a leader (None for a member).

    Same rules as the old overview: only the super-admin sees super-admin rows, the roster (``roles``) skips
    archived people and ``statuses`` includes them. Keys: ``total`` (not archived), ``active``, ``roles`` and
    ``statuses`` (lists of ``{"value", "label", "count"}``, zero rows left out) and ``new_in_period``: how many
    people who are not archived joined between ``start`` and ``end`` (None when no period is given).
    """
    if user.role not in LEADER_ROLES:
        return None
    people = _scope_people(User.objects.all(), user, extension_id)
    if user.role != Role.SUPER_ADMIN:
        people = people.exclude(role=Role.SUPER_ADMIN)
    live = people.exclude(status=Status.ARCHIVED)
    role_counts = dict(live.values_list("role").annotate(n=Count("id")).order_by())
    status_counts = dict(people.values_list("status").annotate(n=Count("id")).order_by())
    new_in_period = None
    if start is not None or end is not None:
        new_in_period = _scope_people(live, user, extension_id, start, end).count()
    return {
        "total": sum(role_counts.values()),
        "active": status_counts.get(Status.ACTIVE.value, 0),
        "roles": [
            {"value": value, "label": label, "count": role_counts[value]}
            for value, label in Role.choices if role_counts.get(value)
        ],
        "statuses": [
            {"value": value, "label": label, "count": status_counts[value]}
            for value, label in Status.choices if status_counts.get(value)
        ],
        "new_in_period": new_in_period,
    }


def extension_stats(user, start=None, end=None):
    """Return the extension comparison for admin and super-admin (None for everyone else).

    Never narrowed by the extension filter: it is the comparison ACROSS extensions. Keys: ``total`` (not
    archived), ``with_coordinator`` and ``without_coordinator`` (a coordinator counts only if they can sign
    in, the same rule as prayer), ``silent`` (extensions with no uploaded sheet in the last ``SILENT_DAYS``
    days, as ``{"id", "name"}``), ``silent_days``, ``attendance_per_extension`` and ``people_per_extension``
    (top ``EXTENSION_BAR_LIMIT`` as ``{"name", "count"}``) and ``people_more`` (extensions not listed there).
    """
    if user.role not in FILTER_ROLES:
        return None
    today = timezone.localdate()
    live = Extension.objects.filter(archived_at__isnull=True)
    total = live.count()

    coordinators = User.objects.filter(role=Role.COORDINATOR, extension_id=OuterRef("pk")).filter(signin_filter())
    with_coordinator = live.filter(Exists(coordinators)).count()

    recent_sheets = _scope_attendance(user, None, today - timedelta(days=SILENT_DAYS), today).filter(
        extension_id=OuterRef("pk")
    )
    silent = live.filter(~Exists(recent_sheets)).order_by("name")

    dates = _gathering_dates(user, None, start, end)
    attendance_rows = (
        _scope_attendance(user).filter(attendance_date__in=dates)
        .values("extension__name").annotate(present=Coalesce(Sum("total_present"), 0))
        .order_by("-present", "extension__name")[:EXTENSION_BAR_LIMIT]
    )
    people_rows = (
        live.annotate(n=Count("members", filter=~Q(members__status=Status.ARCHIVED.value)))
        .order_by("-n", "name")[:EXTENSION_BAR_LIMIT]
    )
    people_per_extension = [{"name": title_case(ext.name), "count": ext.n} for ext in people_rows]
    return {
        "total": total,
        "with_coordinator": with_coordinator,
        "without_coordinator": total - with_coordinator,
        "silent": [{"id": ext.pk, "name": title_case(ext.name)} for ext in silent],
        "silent_days": SILENT_DAYS,
        "attendance_per_extension": [
            {"name": title_case(row["extension__name"]), "count": row["present"]} for row in attendance_rows
        ],
        "people_per_extension": people_per_extension,
        "people_more": max(0, total - len(people_per_extension)),
    }


def giving_stats(user, extension_id=None, start=None, end=None):
    """Return the giving numbers for a leader (None for a member). Amounts are Decimal, never float.

    Only finalized reports on uploaded sheets, FROZEN ``tithes_total`` and ``offering_amount``, reports on the
    same date added together. Keys: ``reports`` (count), ``tithes`` and ``offering`` (totals), ``total``
    (both), their ``*_display`` text with ``CURRENCY``, and ``trend`` (one row per gathering date, oldest
    first: ``date``, ``label``, ``tithes``, ``offering``, ``total``). ``labels`` and ``totals`` feed a sparkline.
    Amounts are shown on leader dashboards only and are never written to ``audit_log`` or ``notification.data``.
    """
    if user.role not in LEADER_ROLES:
        return None
    zero = Decimal("0")
    dates = _gathering_dates(user, extension_id, start, end)
    reports = _scope_tithes(user, extension_id).filter(attendance__attendance_date__in=dates)
    rows = reports.values("attendance__attendance_date").annotate(
        tithes=Sum("tithes_total"), offering=Sum("offering_amount"), n=Count("pk")
    ).order_by()
    by_date = {row["attendance__attendance_date"]: row for row in rows}

    trend = []
    for day in dates:
        row = by_date.get(day, {})
        tithes, offering = row.get("tithes") or zero, row.get("offering") or zero
        trend.append({"date": day, "label": _day_label(day), "tithes": tithes, "offering": offering, "total": tithes + offering})
    tithes_sum = sum((t["tithes"] for t in trend), zero)
    offering_sum = sum((t["offering"] for t in trend), zero)
    return {
        "reports": sum(row["n"] for row in by_date.values()),
        "tithes": tithes_sum,
        "offering": offering_sum,
        "total": tithes_sum + offering_sum,
        "tithes_display": _peso(tithes_sum),
        "offering_display": _peso(offering_sum),
        "total_display": _peso(tithes_sum + offering_sum),
        "trend": trend,
        "labels": [t["label"] for t in trend],
        "totals": [t["total"] for t in trend],
    }


def prayer_summary(user, extension_id=None):
    """Return the prayer card numbers for the people who handle prayer (None for everyone else).

    Scoping is ONLY ``PrayerRequest.objects.inbox_for(user)`` (archived never listed). ``open`` counts requests
    still waiting for a reply, ``replied`` the ones that have one, and ``latest`` lists the newest
    ``PRAYER_LATEST`` open ones as ``{"id", "label", "status", "status_label", "created_at", "extension"}``.
    ...(keep the rest of the docstring as it is)
    """
    if not can_use_inbox(user):
        return None
    inbox = PrayerRequest.objects.inbox_for(user)
    scope_note = ""
    if extension_id and user.role != Role.COORDINATOR:
        inbox = inbox.filter(extension_id=extension_id)
        has_coordinator = User.objects.filter(role=Role.COORDINATOR, extension_id=extension_id).filter(signin_filter())
        if has_coordinator.exists():
            scope_note = PRAYER_SCOPE_NOTE

    waiting = inbox.filter(status=PrayerRequest.Status.OPEN)
    newest = waiting.select_related("user", "extension").order_by("-created_at", "-pk")[:PRAYER_LATEST]
    return {
        "open": waiting.count(),
        "replied": inbox.filter(status=PrayerRequest.Status.REPLIED).count(),
        "latest": [
            {
                "id": req.pk,
                "label": sender_label(req, user),
                "status": req.status,
                "status_label": req.get_status_display(),
                "created_at": req.created_at,
                "extension": title_case(req.extension.name),
            }
            for req in newest
        ],
        "scope_note": scope_note,
    }


def upcoming_events(user, limit=5):
    """Return the next events the person may see, soonest first, as plain dicts.

    Scoping is ONLY ``events.permissions.visible_events``. Upcoming means ``Coalesce(end_at, start_at) >= now``
    (an event still running counts); cancelled events are left out. Each row: ``id``, ``title``, ``start_at``,
    ``end_at``, ``is_global`` and ``extension`` (the extension name, "" for a global event).
    """
    now = timezone.now()
    soonest = (
        visible_events(user).annotate(ends_at=Coalesce("end_at", "start_at")).filter(ends_at__gte=now)
        .select_related("extension").order_by("start_at", "pk")
    )
    # `is_cancelled` is a Python property on Event, so cancelled rows are dropped here; the bounded fetch
    # keeps a long list of cancelled events from being loaded.
    events = [event for event in soonest[: limit * 4] if not event.is_cancelled][:limit]
    return [
        {
            "id": event.pk,
            "title": event.title,
            "start_at": event.start_at,
            "end_at": event.end_at,
            "is_global": event.extension_id is None,
            "extension": title_case(event.extension.name) if event.extension_id else "",
        }
        for event in events
    ]


# ------------------------------------------------- what the leader pages draw (Brick 2)
def dashboard_tiles(attendance, people, extensions, period_start=None, links=None):
    """Return the four headline tiles of a leader dashboard as ``{"label", "value", "hint"}`` dicts.

    People, the last gathering, average attendance, and either how many extensions reported lately (admin and
    super-admin: ``extensions`` is set) or the first-timers total (a coordinator has no extension comparison).
    Every number is also text, so no tile depends on color. ``links`` (``card_links``) gives each tile a ``url``: people
    to the people list, the gathering and attendance tiles (and first timers) to the attendance sheets, extensions
    reporting to the extensions list; "" without ``links``.
    """
    if not attendance or not people:
        return []
    links = links or {}
    count = attendance["count"]
    latest = attendance["latest"]
    joined = people["new_in_period"]
    gatherings = f"{count} gathering{'' if count == 1 else 's'}"

    tiles = [
        {
            "label": "People",
            "value": people["total"],
            "hint": f"{joined} new since {_day_label(period_start)}" if joined is not None and period_start else f"{people['active']} active",
            "url": links.get("people", ""),
        },
        {
            "label": "Last gathering",
            "value": latest["present"] if latest else "–",
            "hint": latest["label"] if latest else "No uploaded sheets yet",
            "url": links.get("attendance", ""),
        },
        {
            "label": "Average attendance",
            "value": f"{attendance['average']:g}" if count else "–",
            "hint": f"Over {gatherings}" if count else "No uploaded sheets yet",
            "url": links.get("attendance", ""),
        },
    ]
    if extensions:
        reporting = extensions["total"] - len(extensions["silent"])
        tiles.append({
            "label": "Extensions reporting",
            "value": f"{reporting} of {extensions['total']}",
            "hint": f"Sent a sheet in the last {extensions['silent_days']} days",
            "url": links.get("extensions", ""),
        })
    else:
        tiles.append({
            "label": "First-timers",
            "value": attendance["first_timers"],
            "hint": f"Over {gatherings}" if count else "No uploaded sheets yet",
            "url": links.get("attendance", ""),
        })
    return tiles


def _ellipsize(text, limit=LABEL_LIMIT):
    """Return ``text`` cut to ``limit`` characters with an ellipsis (SVG text cannot truncate itself)."""
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _donut_chart(title, caption, rows):
    """Return donut geometry plus its title, caption and a one-line text description (for ``aria-label``)."""
    donut = charts.donut([(row["label"], row["count"]) for row in rows], radius=60, stroke=22)
    summary = ", ".join(f"{item['label']} {item['value']}" for item in donut["items"])
    return {**donut, "title": title, "caption": caption, "aria": f"{title}: {summary}" if summary else f"{title}: no data"}


def _hbar_chart(title, caption, rows, more=0):
    """Return horizontal-bar geometry for ``{"name", "count"}`` rows, with long names shortened.

    Each drawn row keeps the full name in ``full`` (the tooltip) and the number in ``value`` (the text).
    """
    chart = charts.hbar([(_ellipsize(row["name"]), row["count"]) for row in rows], max_width=HBAR_WIDTH, row_height=36, gap=10)
    for drawn, source in zip(chart["rows"], rows):
        drawn["full"] = source["name"]
    summary = ", ".join(f"{row['name']} {row['count']}" for row in rows)
    return {**chart, "title": title, "caption": caption, "more": more, "aria": f"{title}: {summary}" if summary else f"{title}: no data"}


def leader_charts(attendance, people, extensions, giving, period_given=False, links=None):
    """Return every leader chart as a prepared dict for the ``_chart_*`` partials (None for a member).

    Keys: ``attendance_line``, ``attendance_columns`` (men and women by age network: the newest gathering, or
    every gathering when a period was asked for), ``donut_roles``, ``donut_status``, ``giving_spark`` and, for
    admin and super-admin only, ``people_bars`` and ``attendance_bars`` (None for a coordinator). Each dict is the
    geometry from ``charts.py`` plus ``title``, ``caption`` and ``aria`` (the whole chart as one sentence); the
    drawn values are always in the dict too, so the partials can print them as text. ``links`` (``card_links``) adds a
    ``url`` to each chart (its title then links to the page behind it: attendance charts to the attendance sheets,
    the donuts to the people list, people per extension to the extensions list); "" without ``links``.
    """
    if attendance is None or people is None:
        return None
    links = links or {}
    points, count = attendance["points"], attendance["count"]

    line = charts.line_chart(attendance["values"], attendance["labels"], CHART_WIDTH, CHART_HEIGHT)
    line.update(
        title="Attendance",
        caption="Total present per gathering in the period" if period_given else f"Total present, last {count} gathering{'' if count == 1 else 's'}",
        aria="Attendance: " + (", ".join(f"{p['label']} {p['present']}" for p in points) if points else "no uploaded sheets yet"),
    )

    network = attendance["network"] if period_given else attendance["network_latest"]
    series = [row["label"] for row in network["rows"]]
    groups = [(col["label"], [row["values"][i] for row in network["rows"]]) for i, col in enumerate(network["columns"])]
    columns = charts.column_groups(groups, CHART_WIDTH, CHART_HEIGHT)
    latest = attendance["latest"]
    for group in columns["groups"]:
        for bar in group["bars"]:
            bar["series_label"] = series[bar["series"]] if bar["series"] < len(series) else ""   # for the tooltip
            bar["cx"] = round(bar["x"] + bar["width"] / 2, 2)                                      # centre, for the value text
    parts = [
        f"{label}: " + ", ".join(f"{name} {bar['value']:g}" for name, bar in zip(series, group["bars"]))
        for (label, _values), group in zip(groups, columns["groups"])
    ]
    columns.update(
        title="Men and women by age group",
        caption="Every gathering in the period" if period_given else (f"Latest gathering, {latest['label']}" if latest else "No uploaded sheets yet"),
        series=series,
        aria="Men and women by age group: " + ("no data" if columns["empty"] else "; ".join(parts)),
    )

    spark = charts.sparkline(giving["totals"], 120, 32) if giving else None
    if spark is not None:
        spark["items"] = [{"label": row["label"], "display": _peso(row["total"])} for row in giving["trend"]]
        spark["aria"] = "Giving per gathering: " + (", ".join(f"{i['label']} {i['display']}" for i in spark["items"]) or "no reports yet")

    line["url"] = columns["url"] = links.get("attendance", "")
    result = {
        "attendance_line": line,
        "attendance_columns": columns,
        "donut_roles": _donut_chart("People by role", "Not archived", people["roles"]),
        "donut_status": _donut_chart("People by status", "Everyone, including archived", people["statuses"]),
        "giving_spark": spark,
        "people_bars": _hbar_chart(
            "People per extension", "Biggest, not archived. Not changed by the picker.", extensions["people_per_extension"], extensions["people_more"]
        ) if extensions else None,
        "attendance_bars": _hbar_chart(
            "Attendance per extension", "Total present in the period. Not changed by the picker.", extensions["attendance_per_extension"]
        ) if extensions else None,
    }
    for key, target in (("donut_roles", "people"), ("donut_status", "people"), ("people_bars", "extensions"), ("attendance_bars", "attendance")):
        if result[key] is not None:
            result[key]["url"] = links.get(target, "")
    return result


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


# ------------------------------------------------- the personal dashboard (Brick 3)
def get_personal_context(user, version_code, path):
    """Return the ``dashboard`` dict of the personal page (every role; only the person's OWN data).

    No id comes from the request and no extension filter is read: nothing here is scoped by anyone but ``user``.
    Reuses the shared pieces (verse, bookmark, streaks, goals today) and adds ``checkin`` (7-day strip),
    ``buddy``, ``journey``, ``upcoming_events`` and ``events_url``. ``streaks``, ``goals_today`` and ``checkin``
    are None for people who cannot use goals, so the template draws no goals card for them.

    ``path`` is accepted so the call looks like ``get_dashboard_context``; the page has no quick links.
    """
    today = timezone.localdate()
    overview = faith_overview.global_overview(user, today) if can_use_goals(user) else None
    return {
        "greeting_name": title_case(user.first_name),
        "verse": _verse_of_the_day(version_code),
        "continue_reading": _continue_reading(user),
        "bible_url": _safe_reverse("bible:reader"),
        "start_url": _bible_chapter_url("Gen", 1),
        "streaks": _streaks(user, today),
        "goals_today": _goals_today(user, today),
        "checkin": checkin_week(overview),
        "buddy": buddy_summary(user),
        "journey": journey_summary(user, overview),
        "upcoming_events": upcoming_events(user, 5),
        "events_url": _safe_reverse("events:calendar") or _safe_reverse("events:list") or _safe_reverse("events:index"),
    }


def checkin_week(overview):
    """Return the 7-day check-in strip (grouped columns: opened the app, read the Bible), or None without goals.

    ``overview`` is ``faith.overview.global_overview(user, today)`` (or None). Its ``activity`` rows carry one
    cell per day with a ``state`` of done, missed or open. A day counts as 1 when ``done`` and 0 otherwise, so
    "today, not yet" is a 0 and never a miss. Every day is also returned as text in ``rows`` and the totals are
    in ``aria``, so no meaning depends on the bars.
    """
    if not overview:
        return None
    by_key = {row["key"]: row["cells"][-CHECKIN_DAYS:] for row in overview.get("activity", [])}
    opened_cells, read_cells = by_key.get("open", []), by_key.get("read", [])
    flags = [(o["date"], o["state"] == "done", r["state"] == "done") for o, r in zip(opened_cells, read_cells)]
    chart = charts.column_groups(
        [(f"{day:%a}", [int(opened), int(read)]) for day, opened, read in flags], width=300, height=120
    )
    for group in chart["groups"]:
        for bar in group["bars"]:
            bar["series_label"] = CHECKIN_SERIES[bar["series"]]
    # No y-axis is drawn on this small strip, so the viewBox starts at the plot's left edge (no empty gutter).
    plot = chart["plot"]
    chart["view_box"] = f"{plot['left'] - 4} 0 {plot['width'] + 8} {chart['height']}"
    opened_days = sum(1 for _d, opened, _r in flags if opened)
    read_days = sum(1 for _d, _o, read in flags if read)
    return {
        "chart": chart,
        "series": list(CHECKIN_SERIES),
        "days": len(flags),
        "opened": opened_days,
        "read": read_days,
        "rows": [{"label": f"{day:%a %b} {day.day}", "opened": opened, "read": read} for day, opened, read in flags],
        "aria": (
            f"Last {len(flags)} days: opened the app on {opened_days} days, read the Bible on {read_days} days."
        ),
    }


def buddy_summary(user):
    """Return the Buddy card numbers for ``user``: ``{"state", "url", ...}``.

    ``state`` is ``live`` (name, extension, since and the partner's two streaks, all from
    ``buddy.services.partner_card``), ``inactive`` (a partnership exists but the partner cannot sign in right
    now: no name, no numbers) or ``none``. Never a goal, note or message of the partner. The unread message count
    is left out on purpose: ``buddy.messaging.unread_count`` is not wired here until it is checked.
    """
    url = _safe_reverse("buddy:home")
    card = buddy_services.partner_card(user)
    if card is not None:
        return {
            "state": "live",
            "url": url,
            "name": card["name"],
            "extension": title_case(card["extension"]) if card["extension"] else "",
            "since": card["since"],
            "streaks": card["streaks"],
        }
    if buddy_services.active_partnership(user) is not None:
        return {"state": "inactive", "url": url}
    return {"state": "none", "url": url}


def _my_attended_services(user, limit):
    """Return up to ``limit`` services the person attended, newest first, as ``{"date", "label"}`` dicts.

    Source: the person's OWN attendance on UPLOADED sheets only (a reopened sheet drops out), the same history
    as the "Attendance" tab of My journey (``attendance:mine``). Only the person's own rows, no id from the request.

    NOT WIRED YET: the attendance models and the ``attendance:mine`` view were not part of the Brick 3 files, so
    this returns an empty list and the card shows its empty state. Replace the body with the same query
    ``attendance:mine`` uses, sliced to ``limit``, and the card fills in with no template change.
    """
    return []


def journey_summary(user, overview=None):
    """Return the "My journey" card numbers: recent services, goals done this week, perfect-day streak.

    ``overview`` is ``faith.overview.global_overview`` (None for people who cannot use goals; ``goals`` is then
    None and the card shows only the services part). ``url`` is the journey hub (its first tab, Goals).
    """
    goals = None
    if overview:
        week, perfect = overview["week"], overview["perfect"]
        goals = {
            "has_goals": overview["has_goals"],
            "done": week["done"],
            "expected": week["expected"],
            "streak": perfect["current"],
            "best_streak": perfect["best"],
        }
    return {
        "services": _my_attended_services(user, JOURNEY_SERVICES),
        "goals": goals,
        "url": _safe_reverse("faith:goals"),
    }


# ------------------------------------------------- the report (Brick 4)
# The period rules shared by the dashboard's quick links and the standalone A4 print page (``report_print.html``,
# context from ``get_report_context``). The print context reuses the dashboard's key names (``filter``, ``period``,
# ``attendance_stats``, ``people_stats``, ``extension_stats``, ``giving_stats``, ``charts``).
PERIOD_PRESETS = (
    ("this_month", "This month"),
    ("last_month", "Last month"),
    ("last_3_months", "Last 3 months"),
    ("this_year", "This year"),
    ("custom", "Custom dates"),
)
PRESET_CODES = {code for code, _label in PERIOD_PRESETS}
DEFAULT_PRESET = "last_3_months"
MAX_PERIOD_DAYS = 366        # a custom period longer than this is "bad" and falls back to the default preset
MAX_LINE_LABELS = 13         # the line chart prints every date and value up to this many points (13 Sundays = the default 3 months); more are thinned
EVENTS_LIMIT = 60            # events listed on the report (a bounded fetch; the period is at most a year)
DASHBOARD_ROUTES = {Role.SUPER_ADMIN: "superadmin", Role.ADMIN: "admin", Role.COORDINATOR: "coordinator"}   # name in ``dashboards:``


def _months_ago(day, months):
    """Return the date ``months`` calendar months before ``day``; the day is clamped (Mar 31 minus 1 month = Feb 28)."""
    index = day.year * 12 + (day.month - 1) - months
    year, month = divmod(index, 12)
    month += 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _preset_range(preset, today):
    """Return ``(start, end)`` of a preset. ``today`` is the Manila date.

    This month and this year run up to today; last month is the whole previous calendar month; "last 3 months"
    is rolling: the day after the same date three months ago, up to today.
    """
    if preset == "this_month":
        return today.replace(day=1), today
    if preset == "last_month":
        last_day = today.replace(day=1) - timedelta(days=1)
        return last_day.replace(day=1), last_day
    if preset == "this_year":
        return date(today.year, 1, 1), today
    return _months_ago(today, 3) + timedelta(days=1), today


def _period_label(start, end):
    """Return the period as text: ``Jul 26 to Oct 25, 2026`` (both years when they differ)."""
    if start.year == end.year:
        return f"{start:%b} {start.day} to {end:%b} {end.day}, {end.year}"
    return f"{start:%b} {start.day}, {start.year} to {end:%b} {end.day}, {end.year}"


def _parse_day(raw):
    """Return a ``date`` from ``YYYY-MM-DD`` text (what ``<input type="date">`` sends), or None for anything else."""
    text = str(raw or "").strip()
    if len(text) != 10 or not text.isascii():
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def parse_period(raw_preset, raw_start, raw_end, today):
    """Validate the report period and return it as a dict; a bad request never raises.

    ``raw_preset`` is one of ``PERIOD_PRESETS`` (``custom`` also reads ``raw_start`` and ``raw_end``). Anything
    else falls back to "last 3 months": an unknown preset, a custom period with a missing or unreadable date,
    an end before the start, or more than ``MAX_PERIOD_DAYS`` days. An empty preset is simply the default.

    Keys: ``preset``, ``start``, ``end`` (dates, Manila), ``label``, ``days`` (inclusive) and ``fell_back``
    (True only when something WAS asked for and was refused, so the page can say so).
    """
    preset = str(raw_preset or "").strip()
    fell_back = False
    start = end = None
    if preset == "custom":
        start, end = _parse_day(raw_start), _parse_day(raw_end)
        if start is None or end is None or end < start or (end - start).days + 1 > MAX_PERIOD_DAYS:
            preset, fell_back = DEFAULT_PRESET, True
    elif preset not in PRESET_CODES:
        preset, fell_back = DEFAULT_PRESET, bool(preset)
    if preset != "custom":
        start, end = _preset_range(preset, today)
    return {
        "preset": preset, "start": start, "end": end, "label": _period_label(start, end),
        "days": (end - start).days + 1, "fell_back": fell_back,
    }


def _flag(raw, default):
    """Read a checkbox sent as ``0`` or ``1`` (the form sends a hidden ``0`` first); missing or odd text is ``default``."""
    text = str(raw or "").strip().lower()
    if text in ("1", "true", "on", "yes"):
        return True
    if text in ("0", "false", "off", "no"):
        return False
    return default


def _label_step(count):
    """Return how many points to skip between printed dates on the line chart (1 = print every one)."""
    return max(1, math.ceil(count / MAX_LINE_LABELS))


def _plural(number, word):
    """Return ``1 gathering`` or ``3 gatherings``."""
    return f"{number} {word}{'' if number == 1 else 's'}"


def _report_query(period, chosen, user, include_giving):
    """Return the VALIDATED querystring of this report, for the Print button and the "Back" link.

    Built from what was accepted, not from what was sent: a refused custom period becomes the default preset, a
    bad or forged ``extension`` is dropped, and a coordinator never carries one at all.
    """
    pairs = [("preset", period["preset"])]
    if period["preset"] == "custom":
        pairs += [("start", period["start"].isoformat()), ("end", period["end"].isoformat())]
    if chosen is not None and user.role in FILTER_ROLES:
        pairs.append(("extension", chosen.pk))
    pairs.append(("giving", "1" if include_giving else "0"))
    return urlencode(pairs)


def _attendance_gap(attendance):
    """Return how many people are in the total but not in Men or Women (no gender or age network recorded)."""
    split = attendance["split"]
    return max(0, attendance["total_present"] - split["men"] - split["women"])


def _period_filter(user, chosen, asked, period_start, period_end, include_giving):
    """Return what the dashboard's period controls draw: quick links, the form values and the Print querystring.

    ``asked`` is ``parse_period`` of the request (None when no preset was sent). Keys: ``preset`` ("" for the default
    view, else the accepted preset), ``links`` (``{"label", "query", "active"}``: one per preset plus the default view,
    each KEEPING the picked extension and the giving switch; "Custom dates" is not a link, it opens the From and To
    fields), ``start`` / ``end`` (those two fields), ``fell_back`` (a preset was sent and refused), ``include_giving``,
    ``giving_query`` (the querystring of the same view with the giving switch FLIPPED: the giving chip is a link),
    ``hidden`` (``(name, value)`` pairs that carry the current period and giving switch through the extension picker's
    form) and ``print_query`` (the VALIDATED querystring of the A4 report: the accepted period, or last 3 months when
    the dashboard shows the default view, since "the last 8 gatherings" is not a date range).
    """
    accepted = asked if (asked is not None and not asked["fell_back"]) else None
    selected = accepted["preset"] if accepted else ""
    keep = []
    if chosen is not None and user.role in FILTER_ROLES:
        keep.append(("extension", chosen.pk))
    keep.append(("giving", "1" if include_giving else "0"))

    links = [{"label": "Last 8 gatherings", "query": urlencode(keep), "active": selected == ""}]
    links += [
        {"label": label, "query": urlencode([("preset", code)] + keep), "active": selected == code}
        for code, label in PERIOD_PRESETS if code != "custom"
    ]
    today = timezone.localdate()

    def state(giving):
        """The current period (as accepted) and a giving switch value, as ``(name, value)`` pairs."""
        pairs = []
        if selected:
            pairs.append(("preset", selected))
            if selected == "custom":
                pairs += [("start", accepted["start"].isoformat()), ("end", accepted["end"].isoformat())]
        pairs.append(("giving", "1" if giving else "0"))
        return pairs

    extension_pair = [("extension", chosen.pk)] if (chosen is not None and user.role in FILTER_ROLES) else []
    return {
        "preset": selected,
        "links": links,
        "start": period_start or today,
        "end": period_end or today,
        "fell_back": asked is not None and asked["fell_back"],
        "include_giving": include_giving,
        "giving_query": urlencode(state(not include_giving) + extension_pair),
        "hidden": state(include_giving),
        "print_query": _report_query(accepted or parse_period("", None, None, today), chosen, user, include_giving),
    }


def events_in_period(user, start, end, limit=EVENTS_LIMIT):
    """Return the events the person may see that START between ``start`` and ``end`` (Manila dates), oldest first.

    Scoping is ONLY ``events.permissions.visible_events``; the extension picker does not change it (the extension
    column says "Global" or the extension). Cancelled events are left out. Each row: ``id``, ``title``,
    ``start_at``, ``end_at``, ``is_global``, ``extension`` ("" for a global event) and ``upcoming`` (it has not
    ended yet: ``Coalesce(end_at, start_at) >= now``, the same rule as ``upcoming_events``).
    """
    now = timezone.now()
    rows = (
        visible_events(user).filter(start_at__date__gte=start, start_at__date__lte=end)
        .select_related("extension").order_by("start_at", "pk")
    )
    # `is_cancelled` is a Python property (see upcoming_events), so cancelled rows are dropped here; the bounded
    # fetch keeps a long list of cancelled events from being loaded.
    events = [event for event in rows[: limit * 2] if not event.is_cancelled][:limit]
    return [
        {
            "id": event.pk,
            "title": event.title,
            "start_at": event.start_at,
            "end_at": event.end_at,
            "is_global": event.extension_id is None,
            "extension": title_case(event.extension.name) if event.extension_id else "",
            "upcoming": (event.end_at or event.start_at) >= now,
        }
        for event in events
    ]


def extension_table(user, start, end):
    """Return one row per extension for the print report's extension table (admin and super-admin; None otherwise).

    Rows (name order): ``name``, ``people`` (not archived), ``gatherings`` (distinct dates with an uploaded sheet in
    the period) and ``present`` (frozen ``total_present``, sheets on one date added together). Scoping is
    ``_scope_attendance`` like every other number. Like ``extension_stats`` it lists every extension.
    """
    if user.role not in FILTER_ROLES:
        return None
    live = (
        Extension.objects.filter(archived_at__isnull=True)
        .annotate(n=Count("members", filter=~Q(members__status=Status.ARCHIVED.value)))
        .order_by("name")
    )
    sheets = (
        _scope_attendance(user, None, start, end).values("extension_id")
        .annotate(present=Coalesce(Sum("total_present"), 0), gatherings=Count("attendance_date", distinct=True))
        .order_by()
    )
    by_extension = {row["extension_id"]: row for row in sheets}
    return [
        {
            "name": title_case(ext.name),
            "people": ext.n,
            "gatherings": by_extension.get(ext.pk, {}).get("gatherings", 0),
            "present": by_extension.get(ext.pk, {}).get("present", 0),
        }
        for ext in live
    ]


def _network_table(network):
    """Return the men and women by age network table with its totals, from ``attendance_stats()["network"]``.

    Keys: ``columns`` (``{"code", "label"}``), ``rows`` (``{"label", "values", "total"}``), ``column_totals`` and
    ``total``. Everything is a frozen number already; this only adds the column sums. ``total`` is the sum of the
    rows drawn, so the table always adds up on paper.
    """
    columns, rows = network["columns"], network["rows"]
    return {
        "columns": columns,
        "rows": [{"label": row["label"], "values": row["values"], "total": row["total"]} for row in rows],
        "column_totals": [sum(row["values"][i] for row in rows) for i in range(len(columns))],
        "total": sum(row["total"] for row in rows),
    }


def _summary_lines(scope_label, period, attendance, people, giving):
    """Return the three plain-language lines under the report header (attendance, the extremes, people and giving)."""
    count = attendance["count"]
    if count:
        high, low = attendance["highest"], attendance["lowest"]
        first = (
            f"{scope_label}, {period['label']}: {_plural(count, 'gathering')} with {attendance['total_present']:,} people "
            f"counted in total, an average of {attendance['average']:g} per gathering."
        )
        second = (
            f"The biggest gathering was {high['label']} ({high['present']:,}) and the smallest was {low['label']} "
            f"({low['present']:,}); {_plural(attendance['first_timers'], 'first timer')} came."
        )
    else:
        first = f"{scope_label}, {period['label']}: no uploaded attendance sheets in this period."
        second = "Attendance and first-timer numbers appear here once sheets are uploaded."
    third = (
        f"There are {people['total']:,} people on the roster ({people['active']:,} active) and "
        f"{people['new_in_period']:,} joined in this period."
    )
    if giving is not None:
        if giving["reports"]:
            third += (
                f" {_plural(giving['reports'], 'giving report')} finalized: tithes {giving['tithes_display']}, "
                f"offering {giving['offering_display']}."
            )
        else:
            third += " No giving reports were finalized in this period."
    return [first, second, third]


def get_report_context(user, params, today=None):
    """Return the ``report`` dict for the standalone A4 print page (leaders only).

    ``params`` is ``request.GET`` (or any mapping with ``get``); everything in it is untrusted text and is
    validated here: ``preset`` / ``start`` / ``end`` by ``parse_period``, ``extension`` by ``resolve_extension``
    (a coordinator always gets their own, admin and super-admin only a real, non-archived one) and ``giving`` by
    ``_flag`` (default: included). Nothing is written, logged or recounted: every number is the frozen value of
    an uploaded sheet (``attendance_stats``...), so it equals the dashboard's for the same scope and period.

    Keys shared with the dashboard context: ``filter``, ``period``, ``tiles``, ``charts``, ``attendance_stats``,
    ``people_stats``, ``extension_stats``, ``giving_stats`` (None when giving is not included). Print-only keys:
    ``presets``, ``include_giving``, ``query`` (the validated querystring, also the way back to the dashboard),
    ``dashboard_url``,
    ``prepared_for`` (role label), ``summary_lines``, ``network_table``, ``attendance_gap``, ``extension_table``
    (print table; None for a coordinator or when one extension is picked), ``events`` and ``prayer``
    (open and replied COUNTS and ``scope_note`` only: never request text, never a name).
    """
    if user.role not in LEADER_ROLES:
        raise PermissionDenied   # the views already check the role; this keeps the service safe on its own
    today = today or timezone.localdate()
    period = parse_period(params.get("preset"), params.get("start"), params.get("end"), today)
    start, end = period["start"], period["end"]
    chosen = resolve_extension(user, params.get("extension"))
    extension_id = chosen.pk if chosen else None
    include_giving = _flag(params.get("giving"), default=True)

    attendance = attendance_stats(user, extension_id, start, end)
    people = people_stats(user, extension_id, start, end)
    extensions = extension_stats(user, start, end)
    giving = giving_stats(user, extension_id, start, end) if include_giving else None
    filter_info = _filter_info(user, chosen)

    drawn = leader_charts(attendance, people, extensions, giving, period_given=True)
    drawn["attendance_line"]["label_step"] = _label_step(attendance["count"])

    prayer = prayer_summary(user, extension_id)
    return {
        "filter": filter_info,
        "period": period,
        "presets": PERIOD_PRESETS,
        "include_giving": include_giving,
        "query": _report_query(period, chosen, user, include_giving),
        "prepared_for": dict(Role.choices).get(user.role, ""),
        "summary_lines": _summary_lines(filter_info["label"], period, attendance, people, giving),
        "tiles": dashboard_tiles(attendance, people, extensions, start),
        "charts": drawn,
        "attendance_stats": attendance,
        "network_table": _network_table(attendance["network"]),
        # people in the totals who have no gender or age network yet (so men + women is less than the total)
        "attendance_gap": _attendance_gap(attendance),
        "dashboard_url": _safe_reverse(f"dashboards:{DASHBOARD_ROUTES.get(user.role, '')}"),
        "people_stats": people,
        "extension_stats": extensions,
        "extension_table": extension_table(user, start, end) if extensions is not None and chosen is None else None,
        "events": events_in_period(user, start, end),
        "giving_stats": giving,
        "prayer": (
            None if prayer is None
            else {"open": prayer["open"], "replied": prayer["replied"], "scope_note": prayer["scope_note"]}
        ),
    }
