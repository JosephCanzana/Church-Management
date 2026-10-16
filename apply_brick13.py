#!/usr/bin/env python3
"""apply_brick13.py: applies every Brick 13 (event reminders) change to the Church-Management repo.

Run it from the repo root (the folder with manage.py):

    python apply_brick13.py --dry-run     # show what would change, write nothing
    python apply_brick13.py               # apply
    python apply_brick13.py --skip-trim   # apply, without trimming old EventReminder rows

What it does (steps 1 to 4 of the plan):
  1. events/models.py        adds EventReminder
  2. events/services.py      reminder helpers, send_reminders, push on change/cancel notices
  3. events command          management/commands/send_event_reminders.py
     notifications/display.py  event_changed slug + "starts in about N hours" text
  4. theming services/forms/views replaced; settings.html gets the Reminders card and the push
     card moves OUT of the form (nested-form fix); _push_card.html wording
  + tests: events/tests/test_reminders.py and theming/tests/test_reminder_setting.py

Safe to run twice: a change that is already in place is skipped. All-or-nothing: if any anchor
text is not found (the file was edited differently), NOTHING is written and the problem is listed.
Files are found by path, or by their last two path parts (e.g. "theming/settings.html").
After it runs: makemigrations, run the tests, rebuild Tailwind (the script prints the commands).
"""
import argparse
import re
import sys
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "env", "__pycache__", "staticfiles"}


# ============================================================ plumbing
class Plan:
    def __init__(self, root):
        self.root = root
        self.files = {}       # Path -> {"text", "orig", "crlf"} for files that already exist
        self.new = {}         # Path -> content for files to create
        self.notes, self.warnings, self.errors = [], [], []

    def rel(self, path):
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return str(path)

    def load(self, path):
        if path not in self.files:
            raw = path.read_bytes().decode("utf-8")
            text = raw.replace("\r\n", "\n")
            self.files[path] = {"text": text, "orig": text, "crlf": "\r\n" in raw}
        return self.files[path]

    def locate(self, rel):
        """Find an existing file: exact path first, then any file whose path ends in the same last two parts."""
        direct = self.root / rel
        if direct.is_file():
            return direct
        tail = "/".join(Path(rel).parts[-2:])
        hits = [
            p for p in self.root.rglob(Path(rel).name)
            if p.is_file() and not (set(p.parts) & SKIP_DIRS) and p.as_posix().endswith(tail)
        ]
        if len(hits) == 1:
            return hits[0]
        self.errors.append(
            f"cannot find {rel}" if not hits else f"{rel} matches several files: {[self.rel(h) for h in hits]}"
        )
        return None

    # ---- edits
    def edit(self, path, old, new, label):
        """Replace `old` (must occur exactly once) with `new`. Skipped when `new` is already there."""
        if path is None:
            return
        f = self.load(path)
        if new in f["text"]:
            self.notes.append(f"already applied: {label}")
            return
        found = f["text"].count(old)
        if found != 1:
            self.errors.append(f"{label}: expected the anchor once in {self.rel(path)}, found {found}")
            return
        f["text"] = f["text"].replace(old, new)
        self.notes.append(f"edit: {label}")

    def edit_regex(self, path, pattern, new, label, done_marker):
        if path is None:
            return
        f = self.load(path)
        if done_marker in f["text"] and not re.search(pattern, f["text"], re.M):
            self.notes.append(f"already applied: {label}")
            return
        text, count = re.subn(pattern, lambda _m: new, f["text"], count=0, flags=re.M)
        if count != 1:
            self.errors.append(f"{label}: expected the anchor once in {self.rel(path)}, found {count}")
            return
        f["text"] = text
        self.notes.append(f"edit: {label}")

    def append(self, path, marker, block, label):
        if path is None:
            return
        f = self.load(path)
        if marker in f["text"]:
            self.notes.append(f"already applied: {label}")
            return
        f["text"] = f["text"].rstrip("\n") + "\n\n\n" + block.strip("\n") + "\n"
        self.notes.append(f"append: {label}")

    def replace_file(self, path, content, done_marker, expect, label):
        """Replace a whole existing file. Refuses when it does not look like the file we expect."""
        if path is None:
            return
        f = self.load(path)
        if done_marker in f["text"]:
            self.notes.append(f"already applied: {label}")
            return
        if expect not in f["text"]:
            self.errors.append(f"{label}: {self.rel(path)} does not contain {expect!r}; not replacing it")
            return
        f["text"] = content.strip("\n") + "\n"
        self.notes.append(f"replace: {label}")

    def create(self, path, content, label):
        content = content.strip("\n") + "\n"
        if path.exists():
            if path.read_bytes().decode("utf-8").replace("\r\n", "\n") == content:
                self.notes.append(f"already applied: {label}")
            else:
                self.warnings.append(f"{self.rel(path)} already exists and differs; left alone ({label})")
            return
        self.new[path] = content
        self.notes.append(f"create: {label}")

    def write(self):
        for path, f in self.files.items():
            if f["text"] != f["orig"]:
                out = f["text"].replace("\n", "\r\n") if f["crlf"] else f["text"]
                path.write_bytes(out.encode("utf-8"))
        for path, content in self.new.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")


# ============================================================ step 1: model
EVENT_REMINDER_MODEL = r'''
class EventReminder(models.Model):
    """One reminder already sent: a person, an event, a lead time and the start time it was for.

    The unique row is what makes the job safe to run twice. A rescheduled event has a new
    `event_start`, so it reminds again; a cancelled one is skipped by the job itself.
    """
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name="reminders")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    lead_minutes = models.PositiveIntegerField()
    event_start = models.DateTimeField()
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "event_reminder"
        constraints = [
            models.UniqueConstraint(
                fields=["event", "user", "lead_minutes", "event_start"],
                name="uq_event_reminder",
            ),
        ]
'''

# ============================================================ step 2: services
SERVICES_REMINDERS = r'''
# ----------------------------------------------------------------- reminders
def _reminder_push(event):
    """Push text: the event title and its start in Manila time. Nothing private."""
    local = timezone.localtime(event.start_at)
    return {
        "title": event.title,
        "body": f"Starts {local:%a, %b %d at %I:%M %p}",
        "url": _event_url(event),
        "tag": f"event-reminder-{event.pk}",
    }


def _pending_by_lead(event, now):
    """{lead_minutes: [user ids]} still to be reminded about `event` right now.

    Due means the person's lead time has been reached and the event has not started. "Reached"
    (not "within this exact quarter hour") so a missed run catches up on the next one.
    Already-sent reminders for this start time are left out.
    """
    ids = event_recipients(event)
    if not ids:
        return {}
    due = {}
    wanted = User.objects.filter(pk__in=ids, settings__event_reminders_enabled=True).values_list(
        "pk", "settings__event_reminder_minutes",
    )
    for user_id, lead in wanted:
        if event.start_at - timedelta(minutes=lead) <= now:
            due.setdefault(lead, []).append(user_id)
    if not due:
        return {}
    done = set(
        EventReminder.objects.filter(
            event=event, event_start=event.start_at, lead_minutes__in=list(due),
        ).values_list("user_id", "lead_minutes")
    )
    pending = {}
    for lead, user_ids in due.items():
        left = [uid for uid in user_ids if (uid, lead) not in done]
        if left:
            pending[lead] = left
    return pending


def _upcoming_event_ids(now):
    """Ids of live, not-cancelled events that have not started and start within the longest lead."""
    return list(
        Event.objects.filter(
            archived_at__isnull=True, cancelled_at__isnull=True,
            start_at__gt=now, start_at__lte=now + timedelta(minutes=REMINDER_MAX_MINUTES),
        ).order_by("start_at").values_list("pk", flat=True)
    )


def due_reminders(now=None):
    """What the job would send now, without sending: [(event_id, {lead_minutes: [user ids]})].

    Read-only (no locks), for tests and dry runs. `send_reminders` re-checks under a lock.
    """
    now = now or timezone.now()
    out = []
    for event in Event.objects.filter(pk__in=_upcoming_event_ids(now)).order_by("start_at"):
        pending = _pending_by_lead(event, now)
        if pending:
            out.append((event.pk, pending))
    return out


def send_reminders(now=None):
    """Send every reminder that is due. Returns {"events": n, "reminders": n} (counts only).

    One transaction per event: lock it, re-check it is still upcoming and not cancelled, record
    an EventReminder row per person and send the bell items (and pushes, after commit). Running
    it twice in a row sends nothing new. Writes ONE audit row (source system_job, counts only),
    and only when something was sent, so the 15-minute job does not fill the trail.
    """
    now = now or timezone.now()
    result = {"events": 0, "reminders": 0}
    for event_id in _upcoming_event_ids(now):
        try:
            with transaction.atomic():
                locked = _lock(event_id)
                if locked.is_cancelled or locked.start_at <= now:
                    continue
                pending = _pending_by_lead(locked, now)
                if not pending:
                    continue
                sent = 0
                for lead, user_ids in pending.items():
                    EventReminder.objects.bulk_create(
                        [
                            EventReminder(
                                event=locked, user_id=uid, lead_minutes=lead,
                                event_start=locked.start_at,
                            )
                            for uid in user_ids
                        ],
                        ignore_conflicts=True,
                    )
                    sent += notify_many(
                        user_ids, EVENT_REMINDER,
                        entity_type="event", entity_id=locked.pk,
                        data={
                            "title": locked.title, "lead_minutes": lead,
                            "starts_at": locked.start_at.isoformat(),
                        },
                        push=_reminder_push(locked),
                    )
                result["events"] += 1
                result["reminders"] += sent
        except ServiceError:
            continue   # the event vanished between the list and the lock
@@TRIM@@
    if result["reminders"]:
        log_action("event.reminders_sent", source=AuditLog.Source.SYSTEM_JOB, after=result)
    return result
'''

TRIM_LINE = (
    "    # tidy up: old reminder rows are no longer needed once the event is long past\n"
    "    EventReminder.objects.filter(event_start__lt=now - timedelta(days=REMINDER_KEEP_DAYS)).delete()"
)

# ============================================================ step 3: command + display
COMMAND = r'''
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
'''

DISPLAY_LEAD_HELPER = r'''def _lead_text(minutes):
    """'in about 1 day', 'in about 3 hours'... from a reminder's lead time in minutes."""
    if not isinstance(minutes, int) or minutes <= 0:
        return "soon"
    for size, word in ((1440, "day"), (60, "hour"), (1, "minute")):
        if minutes % size == 0:
            count = minutes // size
            return f"in about {count} {word}{'' if count == 1 else 's'}"


def describe(notification):
'''

# ============================================================ step 4: theming
THEMING_SERVICES = r'''
"""theming.services: reading and changing a person's own display settings.

Personal preferences (color mode, mobile menu style, event reminders) are deliberately NOT
written to audit_log: they are not an administrative event (build plan, rule 9.6).
"""
from django.db import transaction

from apps.core.services import ServiceError

from .models import UserSettings

# How early a reminder can come, in minutes (brick 13). "Off" is event_reminders_enabled=False.
REMINDER_MINUTES = (1440, 180, 60)


def get_user_settings(user):
    """Return the person's settings row, creating it with the defaults if it is missing.

    The post_save signal and the backfill migration normally guarantee the row; this is the
    safety net so the settings page never fails for an account that somehow has none.
    """
    row, _ = UserSettings.objects.get_or_create(user=user)
    return row


def update_user_settings(
    user, *, color_mode, mobile_nav_style,
    event_reminders_enabled=None, event_reminder_minutes=None,
):
    """Save the signed-in person's own display and reminder settings.

    Returns (row, changed). Only `user` is ever edited: the caller passes request.user and
    nothing from the request names another account. Raises ServiceError for a missing
    account or a value outside the allowed choices (the form checks the same, this is the
    second guard).

    `event_reminders_enabled` / `event_reminder_minutes` left as None are not touched. Turning
    reminders off passes enabled=False and minutes=None, so the lead time is remembered.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        raise ServiceError("You must be signed in to change your settings.")
    if color_mode not in UserSettings.ColorMode.values:
        raise ServiceError("Choose light, dark or follow device.")
    if mobile_nav_style not in UserSettings.MobileNav.values:
        raise ServiceError("Choose the bottom bar or the hamburger menu.")
    if event_reminders_enabled is not None and not isinstance(event_reminders_enabled, bool):
        raise ServiceError("Choose 1 day, 3 hours, 1 hour or off.")
    if event_reminder_minutes is not None and event_reminder_minutes not in REMINDER_MINUTES:
        raise ServiceError("Choose 1 day, 3 hours, 1 hour or off.")

    new_values = {"color_mode": color_mode, "mobile_nav_style": mobile_nav_style}
    if event_reminders_enabled is not None:
        new_values["event_reminders_enabled"] = event_reminders_enabled
    if event_reminder_minutes is not None:
        new_values["event_reminder_minutes"] = event_reminder_minutes

    with transaction.atomic():
        row, _ = UserSettings.objects.select_for_update().get_or_create(user=user)
        changed_fields = [name for name, value in new_values.items() if getattr(row, name) != value]
        if changed_fields:
            for name in changed_fields:
                setattr(row, name, new_values[name])
            # updated_at is auto_now, so it must be listed to be written.
            row.save(update_fields=[*changed_fields, "updated_at"])
    return row, bool(changed_fields)
'''

THEMING_FORMS = r'''
"""theming.forms: input validation for the personal settings page.

Shape checks only (is the value one of the allowed choices). Saving is done by
`theming.services.update_user_settings`, which checks again.
"""
from django import forms

from .models import UserSettings
from .services import REMINDER_MINUTES

COLOR_MODE_HELP = {
    UserSettings.ColorMode.LIGHT: "Always use the light colors.",
    UserSettings.ColorMode.DARK: "Always use the dark colors.",
    UserSettings.ColorMode.SYSTEM: "Match your phone or computer, and change when it does.",
}
MOBILE_NAV_HELP = {
    UserSettings.MobileNav.BOTTOM: "A bar at the bottom of the screen with your main links.",
    UserSettings.MobileNav.HAMBURGER: "Open the menu from the button at the top of the screen.",
}
REMINDER_LABELS = {1440: "1 day before", 180: "3 hours before", 60: "1 hour before"}
REMINDER_CHOICES = [(str(m), REMINDER_LABELS[m]) for m in REMINDER_MINUTES] + [("off", "Off")]
REMINDER_HELP = {
    "1440": "A day ahead, so you can plan for it.",
    "180": "Three hours before it starts.",
    "60": "One hour before it starts.",
    "off": "No reminders. Changes and cancellations still reach you.",
}
_HELPS = {
    "color_mode": COLOR_MODE_HELP,
    "mobile_nav_style": MOBILE_NAV_HELP,
    "event_reminder": REMINDER_HELP,
}
_REMINDER_ERROR = "Choose 1 day, 3 hours, 1 hour or off."


def reminder_initial(row):
    """The reminder radio to preselect for a settings row: 'off', or the closest allowed lead time."""
    if not row.event_reminders_enabled:
        return "off"
    return str(min(REMINDER_MINUTES, key=lambda m: abs(m - row.event_reminder_minutes)))


class UserSettingsForm(forms.Form):
    """Color mode, mobile menu style and event reminder. All required: no auto-default on a bad value."""

    color_mode = forms.ChoiceField(
        choices=UserSettings.ColorMode.choices,
        widget=forms.RadioSelect,
        error_messages={
            "required": "Choose light, dark or follow device.",
            "invalid_choice": "Choose light, dark or follow device.",
        },
    )
    mobile_nav_style = forms.ChoiceField(
        choices=UserSettings.MobileNav.choices,
        widget=forms.RadioSelect,
        error_messages={
            "required": "Choose the bottom bar or the hamburger menu.",
            "invalid_choice": "Choose the bottom bar or the hamburger menu.",
        },
    )
    event_reminder = forms.ChoiceField(
        choices=REMINDER_CHOICES,
        widget=forms.RadioSelect,
        error_messages={"required": _REMINDER_ERROR, "invalid_choice": _REMINDER_ERROR},
    )

    def clean(self):
        """Turn the single reminder choice into the two values the service takes."""
        data = super().clean()
        choice = data.pop("event_reminder", None)
        if choice == "off":
            data["event_reminders_enabled"] = False
            data["event_reminder_minutes"] = None      # keep the stored lead time
        elif choice is not None:
            data["event_reminders_enabled"] = True
            data["event_reminder_minutes"] = int(choice)
        return data

    def options(self, name):
        """The radio cards for one field: [{value, label, help, checked}, ...] for the template."""
        helps = _HELPS[name]
        current = self[name].value()
        return [
            {"value": value, "label": label, "help": helps.get(value, ""), "checked": value == current}
            for value, label in self.fields[name].choices
        ]
'''

THEMING_VIEWS = r'''
"""theming.views: the signed-in person's own settings page.

Open to every role. The page edits request.user's row and nothing else: no user id is
read from the URL, the query string or the form (build plan, rule 9.1). Views stay thin:
parse, call the service, show a message, redirect.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods

from apps.core.services import ServiceError

from .forms import UserSettingsForm, reminder_initial
from .services import get_user_settings, update_user_settings


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def settings_view(request):
    """Show the settings form (GET) or save it (POST), then redirect so a refresh never resubmits."""
    row = get_user_settings(request.user)
    if request.method == "POST":
        form = UserSettingsForm(request.POST)
        if form.is_valid():
            try:
                _, changed = update_user_settings(request.user, **form.cleaned_data)
            except ServiceError as exc:
                form.add_error(None, str(exc))
            else:
                if changed:
                    messages.success(request, "Your settings were saved.")
                else:
                    messages.info(request, "No changes to save.")
                return redirect("theming:settings")
    else:
        form = UserSettingsForm(initial={
            "color_mode": row.color_mode,
            "mobile_nav_style": row.mobile_nav_style,
            "event_reminder": reminder_initial(row),
        })
    return render(request, "theming/settings.html", {
        "form": form,
        "color_options": form.options("color_mode"),
        "nav_options": form.options("mobile_nav_style"),
        "reminder_options": form.options("event_reminder"),
    })
'''

SETTINGS_REMINDER_CARD = r'''    {# ------------------------------------------------- event reminders #}
    <section class="card" aria-labelledby="reminder-heading">
      <h2 id="reminder-heading" class="text-lg font-semibold tracking-tight text-txt-primary">Event reminders</h2>
      <p class="mt-1 text-base text-txt-secondary">How early you are reminded before an event starts. A reminder can arrive up to 15 minutes after that time. It always appears in the bell; to also get it on your phone or computer, turn on notifications for that device below.</p>

      <fieldset class="mt-5">
        <legend class="label text-base">Remind me</legend>
        <div class="mt-2 grid gap-3 sm:grid-cols-2">
          {% for opt in reminder_options %}
          <div class="relative">
            <input type="radio" name="event_reminder" id="id_event_reminder_{{ opt.value }}" value="{{ opt.value }}"
                   class="peer sr-only" {% if opt.checked %}checked{% endif %}>
            <label for="id_event_reminder_{{ opt.value }}"
                   class="flex h-full min-h-14 cursor-pointer flex-col gap-1 rounded-2xl border border-border bg-surface p-4 pr-10 transition-colors hover:border-border-strong peer-checked:border-accent peer-checked:bg-accent/10 peer-focus-visible:outline peer-focus-visible:outline-2 peer-focus-visible:outline-offset-2 peer-focus-visible:outline-accent">
              <span class="text-base font-medium text-txt-primary">{{ opt.label }}</span>
              <span class="text-base text-txt-secondary">{{ opt.help }}</span>
            </label>
            <span class="pointer-events-none absolute right-3 top-3 hidden text-accent peer-checked:block" aria-hidden="true">
              {% include "includes/icon.html" with name="check-circle" class="size-5" %}
            </span>
          </div>
          {% endfor %}
        </div>
        {% if form.event_reminder.errors %}
          <div class="mt-2 space-y-1 text-base text-error" role="alert">
            {% for error in form.event_reminder.errors %}<p>{{ error }}</p>{% endfor %}
          </div>
        {% endif %}
      </fieldset>
    </section>
'''

SETTINGS_FORM_END_OLD = "  </form>\n</div>\n{% endblock %}"
SETTINGS_FORM_END_NEW = (
    "  </form>\n\n"
    "  {# Outside the settings form on purpose: the card has its own forms (Remove device), and forms cannot nest. #}\n"
    "  {% push_device_card %}\n"
    "</div>\n{% endblock %}"
)

# ============================================================ tests
TEST_REMINDERS = r'''
"""Brick 13: event reminders (due_reminders, send_reminders, the send_event_reminders command)."""
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Status
from apps.audit.models import AuditLog
from apps.notifications.models import Notification
from apps.theming.models import UserSettings

from .. import services
from ..models import EventReminder
from .helpers import archive_user, build_world, in_days, make_event


def reminders_for(user):
    return Notification.objects.filter(user=user, type=services.EVENT_REMINDER)


def set_lead(user, minutes=None, enabled=True):
    values = {"event_reminders_enabled": enabled}
    if minutes is not None:
        values["event_reminder_minutes"] = minutes
    UserSettings.objects.filter(user=user).update(**values)


class ReminderTests(TestCase):
    def setUp(self):
        self.w = build_world()

    def test_default_one_day_reminder(self):
        make_event(self.w.ext_a, start_at=in_days(0, hours=20))
        result = services.send_reminders()
        self.assertEqual(result["events"], 1)
        self.assertEqual(reminders_for(self.w.member_a).count(), 1)

    def test_not_before_the_lead_time(self):
        make_event(self.w.ext_a, start_at=in_days(3))
        self.assertEqual(services.send_reminders(), {"events": 0, "reminders": 0})
        self.assertEqual(reminders_for(self.w.member_a).count(), 0)

    def test_running_twice_sends_nothing_new(self):
        make_event(self.w.ext_a, start_at=in_days(0, hours=20))
        services.send_reminders()
        again = services.send_reminders()
        self.assertEqual(again, {"events": 0, "reminders": 0})
        self.assertEqual(reminders_for(self.w.member_a).count(), 1)
        self.assertEqual(EventReminder.objects.filter(user=self.w.member_a).count(), 1)

    def test_due_reminders_does_not_send(self):
        event = make_event(self.w.ext_a, start_at=in_days(0, hours=20))
        due = services.due_reminders()
        self.assertEqual([event_id for event_id, _ in due], [event.pk])
        self.assertEqual(Notification.objects.count(), 0)

    def test_each_person_uses_their_own_lead(self):
        set_lead(self.w.member_a, 60)
        set_lead(self.w.member_a2, 180)
        make_event(self.w.ext_a, start_at=timezone.now() + timedelta(hours=2, minutes=30))
        services.send_reminders()
        self.assertEqual(reminders_for(self.w.member_a).count(), 0)   # 1 hour not reached
        self.assertEqual(reminders_for(self.w.member_a2).count(), 1)  # 3 hours reached

    def test_one_hour_lead_arrives_when_reached(self):
        set_lead(self.w.member_a, 60)
        make_event(self.w.ext_a, start_at=timezone.now() + timedelta(minutes=59))
        services.send_reminders()
        self.assertEqual(reminders_for(self.w.member_a).count(), 1)

    def test_off_is_skipped(self):
        set_lead(self.w.member_a, enabled=False)
        make_event(self.w.ext_a, start_at=in_days(0, hours=20))
        services.send_reminders()
        self.assertEqual(reminders_for(self.w.member_a).count(), 0)
        self.assertEqual(reminders_for(self.w.member_a2).count(), 1)

    def test_cancelled_event_sends_nothing(self):
        event = make_event(self.w.ext_a, start_at=in_days(0, hours=20))
        services.cancel_event(self.w.coord_a, event)
        Notification.objects.all().delete()
        self.assertEqual(services.send_reminders(), {"events": 0, "reminders": 0})

    def test_started_event_sends_nothing(self):
        make_event(self.w.ext_a, start_at=timezone.now() - timedelta(minutes=5),
                   end_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(services.send_reminders(), {"events": 0, "reminders": 0})

    def test_rescheduled_event_reminds_again(self):
        event = make_event(self.w.ext_a, start_at=in_days(0, hours=20))
        services.send_reminders()
        event.start_at = in_days(0, hours=23)
        event.save(update_fields=["start_at"])
        result = services.send_reminders()
        self.assertEqual(result["events"], 1)
        self.assertEqual(reminders_for(self.w.member_a).count(), 2)

    def test_extension_event_reaches_only_that_extension(self):
        make_event(self.w.ext_a, start_at=in_days(0, hours=20))
        services.send_reminders()
        for person in (self.w.member_a, self.w.member_a2, self.w.coord_a):
            self.assertEqual(reminders_for(person).count(), 1)
        for person in (self.w.member_b, self.w.coord_b, self.w.admin, self.w.super):
            self.assertEqual(reminders_for(person).count(), 0)

    def test_global_event_reaches_every_active_person(self):
        make_event(None, start_at=in_days(0, hours=20))
        services.send_reminders()
        for person in (self.w.member_a, self.w.member_b, self.w.coord_b, self.w.admin, self.w.super):
            self.assertEqual(reminders_for(person).count(), 1)

    def test_suspended_and_archived_are_skipped(self):
        self.w.member_a.status = Status.SUSPENDED
        self.w.member_a.save(update_fields=["status"])
        archive_user(self.w.member_a2)
        make_event(self.w.ext_a, start_at=in_days(0, hours=20))
        services.send_reminders()
        self.assertEqual(reminders_for(self.w.member_a).count(), 0)
        self.assertEqual(reminders_for(self.w.member_a2).count(), 0)
        self.assertEqual(reminders_for(self.w.coord_a).count(), 1)

    def test_bell_item_holds_only_title_and_timing(self):
        event = make_event(self.w.ext_a, title="Prayer night", start_at=in_days(0, hours=20))
        services.send_reminders()
        item = reminders_for(self.w.member_a).get()
        self.assertEqual(item.entity_type, "event")
        self.assertEqual(item.entity_id, event.pk)
        self.assertEqual(set(item.data), {"title", "lead_minutes", "starts_at"})

    def test_job_logs_counts_only_and_only_when_something_was_sent(self):
        services.send_reminders()
        self.assertFalse(AuditLog.objects.filter(action="event.reminders_sent").exists())
        make_event(self.w.ext_a, start_at=in_days(0, hours=20))
        services.send_reminders()
        entry = AuditLog.objects.get(action="event.reminders_sent")
        self.assertEqual(entry.source, AuditLog.Source.SYSTEM_JOB)
        self.assertIsNone(entry.actor_id)
        self.assertEqual(entry.after, {"events": 1, "reminders": 3})


class CommandTests(TestCase):
    def test_command_sends_and_dry_run_does_not(self):
        w = build_world()
        make_event(w.ext_a, start_at=in_days(0, hours=20))
        out = StringIO()
        call_command("send_event_reminders", "--dry-run", stdout=out)
        self.assertIn("dry run", out.getvalue())
        self.assertEqual(Notification.objects.count(), 0)
        call_command("send_event_reminders", stdout=out)
        self.assertEqual(Notification.objects.filter(type=services.EVENT_REMINDER).count(), 3)
'''

TEST_REMINDER_SETTING = r'''
"""Brick 13: the event reminder choice on the Settings page."""
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import Role, Status, User
from apps.audit.models import AuditLog
from apps.core.services import ServiceError

from ..forms import UserSettingsForm, reminder_initial
from ..models import UserSettings
from ..services import get_user_settings, update_user_settings


def make_person():
    return User.objects.create_user(
        password="test-pass-12345", role=Role.MEMBER, status=Status.ACTIVE,
        first_name="Rem", last_name="Tester", must_change_password=False,
    )


BASE = {"color_mode": "system", "mobile_nav_style": "bottom"}


class ReminderServiceTests(TestCase):
    def setUp(self):
        self.user = make_person()

    def test_defaults_are_on_and_one_day(self):
        row = get_user_settings(self.user)
        self.assertTrue(row.event_reminders_enabled)
        self.assertEqual(row.event_reminder_minutes, 1440)

    def test_saves_a_lead_time(self):
        row, changed = update_user_settings(
            self.user, **BASE, event_reminders_enabled=True, event_reminder_minutes=60,
        )
        self.assertTrue(changed)
        self.assertEqual(row.event_reminder_minutes, 60)

    def test_off_keeps_the_stored_lead_time(self):
        update_user_settings(self.user, **BASE, event_reminders_enabled=True, event_reminder_minutes=180)
        row, changed = update_user_settings(
            self.user, **BASE, event_reminders_enabled=False, event_reminder_minutes=None,
        )
        self.assertTrue(changed)
        self.assertFalse(row.event_reminders_enabled)
        self.assertEqual(row.event_reminder_minutes, 180)

    def test_same_values_are_not_a_change(self):
        _, changed = update_user_settings(
            self.user, **BASE, event_reminders_enabled=True, event_reminder_minutes=1440,
        )
        self.assertFalse(changed)

    def test_rejects_other_lead_times(self):
        with self.assertRaises(ServiceError):
            update_user_settings(self.user, **BASE, event_reminders_enabled=True, event_reminder_minutes=90)

    def test_old_call_without_reminder_values_still_works(self):
        row, changed = update_user_settings(self.user, color_mode="dark", mobile_nav_style="bottom")
        self.assertTrue(changed)
        self.assertEqual(row.event_reminder_minutes, 1440)

    def test_nothing_is_written_to_the_audit_log(self):
        before = AuditLog.objects.count()
        update_user_settings(self.user, **BASE, event_reminders_enabled=False, event_reminder_minutes=None)
        self.assertEqual(AuditLog.objects.count(), before)


class ReminderFormTests(TestCase):
    def test_off_maps_to_disabled_without_a_lead_time(self):
        form = UserSettingsForm({**BASE, "event_reminder": "off"})
        self.assertTrue(form.is_valid())
        self.assertIs(form.cleaned_data["event_reminders_enabled"], False)
        self.assertIsNone(form.cleaned_data["event_reminder_minutes"])
        self.assertNotIn("event_reminder", form.cleaned_data)

    def test_lead_time_maps_to_enabled_and_minutes(self):
        form = UserSettingsForm({**BASE, "event_reminder": "180"})
        self.assertTrue(form.is_valid())
        self.assertIs(form.cleaned_data["event_reminders_enabled"], True)
        self.assertEqual(form.cleaned_data["event_reminder_minutes"], 180)

    def test_invalid_and_missing_are_rejected(self):
        self.assertFalse(UserSettingsForm({**BASE, "event_reminder": "30"}).is_valid())
        self.assertFalse(UserSettingsForm(BASE).is_valid())

    def test_initial_choice_follows_the_row(self):
        row = UserSettings(event_reminders_enabled=True, event_reminder_minutes=1440)
        self.assertEqual(reminder_initial(row), "1440")
        row.event_reminder_minutes = 2000          # an old value: closest allowed option
        self.assertEqual(reminder_initial(row), "1440")
        row.event_reminders_enabled = False
        self.assertEqual(reminder_initial(row), "off")


class ReminderPageTests(TestCase):
    def setUp(self):
        self.user = make_person()
        self.client.force_login(self.user)
        self.url = reverse("theming:settings")

    def test_page_shows_the_four_choices_with_the_saved_one_checked(self):
        update_user_settings(self.user, **BASE, event_reminders_enabled=True, event_reminder_minutes=60)
        response = self.client.get(self.url)
        for value in ("1440", "180", "60", "off"):
            self.assertContains(response, f'id="id_event_reminder_{value}"')
        checked = [o["value"] for o in response.context["reminder_options"] if o["checked"]]
        self.assertEqual(checked, ["60"])

    def test_post_saves_and_redirects(self):
        response = self.client.post(self.url, {**BASE, "event_reminder": "180"})
        self.assertRedirects(response, self.url)
        row = UserSettings.objects.get(user=self.user)
        self.assertTrue(row.event_reminders_enabled)
        self.assertEqual(row.event_reminder_minutes, 180)

    def test_post_off_turns_reminders_off(self):
        self.client.post(self.url, {**BASE, "event_reminder": "off"})
        self.assertFalse(UserSettings.objects.get(user=self.user).event_reminders_enabled)

    def test_bad_value_shows_an_error_and_saves_nothing(self):
        response = self.client.post(self.url, {**BASE, "event_reminder": "30"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose 1 day, 3 hours, 1 hour or off.")
        self.assertEqual(UserSettings.objects.get(user=self.user).event_reminder_minutes, 1440)
'''


# ============================================================ main
def main():
    parser = argparse.ArgumentParser(description="Apply the Brick 13 (event reminders) changes.")
    parser.add_argument("--dry-run", action="store_true", help="show the plan, write nothing")
    parser.add_argument("--skip-trim", action="store_true", help="do not trim old EventReminder rows")
    parser.add_argument("--root", default=".", help="repo root (default: current folder)")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not (root / "manage.py").is_file():
        sys.exit(f"manage.py not found in {root}. Run this from the repo root, or pass --root.")
    plan = Plan(root)
    trim = not args.skip_trim

    # ---------------------------------------------------- locate the existing files
    models_py = plan.locate("apps/events/models.py")
    services_py = plan.locate("apps/events/services.py")
    display_py = plan.locate("apps/notifications/display.py")
    push_card = plan.locate("apps/notifications/templates/notifications/_push_card.html")
    t_services = plan.locate("apps/theming/services.py")
    t_forms = plan.locate("apps/theming/forms.py")
    t_views = plan.locate("apps/theming/views.py")
    settings_html = plan.locate("templates/theming/settings.html")
    if plan.errors:
        report(plan, args.dry_run)

    events_dir = models_py.parent
    theming_dir = t_services.parent

    # ---------------------------------------------------- step 1: model
    plan.append(models_py, "class EventReminder", EVENT_REMINDER_MODEL, "events: EventReminder model")

    # ---------------------------------------------------- step 2: services
    s = services_py
    plan.edit(
        s,
        "    event_cancelled  the event was cancelled (important)\n",
        "    event_cancelled  the event was cancelled (important)\n"
        "    event_reminder   an upcoming event, sent by the 15-minute job\n",
        "services: docstring lists event_reminder",
    )
    plan.edit(
        s, "from django.db import transaction\n",
        "from datetime import timedelta\n\nfrom django.db import transaction\n",
        "services: import timedelta",
    )
    plan.edit(
        s, "from apps.audit.services import log_action\n",
        "from apps.audit.models import AuditLog\nfrom apps.audit.services import log_action\n",
        "services: import AuditLog",
    )
    plan.edit(
        s, "from .models import Event\n", "from .models import Event, EventReminder\n",
        "services: import EventReminder",
    )
    constants = (
        'EVENT_CANCELLED = "event_cancelled"\n'
        'EVENT_REMINDER = "event_reminder"\n'
        "REMINDER_MAX_MINUTES = 43200   # same ceiling as UserSettings.event_reminder_minutes\n"
    )
    if trim:
        constants += "REMINDER_KEEP_DAYS = 30        # EventReminder rows older than this are trimmed by send_reminders\n"
    plan.edit(s, 'EVENT_CANCELLED = "event_cancelled"\n', constants, "services: reminder constants")
    plan.edit(
        s,
        '    return list(people.values_list("pk", flat=True))\n',
        '    return list(people.values_list("pk", flat=True))\n\n\n'
        'def _event_url(event):\n'
        '    """The event page, as a path on this site (used by push messages)."""\n'
        '    return f"/events/{event.pk}/"\n',
        "services: _event_url helper",
    )
    plan.edit(
        s,
        '                data={"title": locked.title, "changes": changes},\n'
        '                audit_log_id=entry.pk,\n',
        '                data={"title": locked.title, "changes": changes},\n'
        '                audit_log_id=entry.pk,\n'
        '                push={\n'
        '                    "title": locked.title,\n'
        '                    "body": f"The {\' and \'.join(changes)} changed.",\n'
        '                    "url": _event_url(locked), "tag": f"event-{locked.pk}",\n'
        '                },\n',
        "services: update_event also pushes (13.10)",
    )
    plan.edit(
        s,
        '            importance=Notification.Importance.IMPORTANT,\n'
        '            audit_log_id=entry.pk,\n',
        '            importance=Notification.Importance.IMPORTANT,\n'
        '            audit_log_id=entry.pk,\n'
        '            push={\n'
        '                "title": locked.title, "body": "This event was cancelled.",\n'
        '                "url": _event_url(locked), "tag": f"event-{locked.pk}",\n'
        '            },\n',
        "services: cancel_event also pushes (13.10)",
    )
    block = SERVICES_REMINDERS.replace("@@TRIM@@", TRIM_LINE if trim else "").replace("\n\n    if result", "\n    if result")
    plan.append(s, "def send_reminders(", block, "services: due_reminders / send_reminders")

    # ---------------------------------------------------- step 3: command + bell text
    commands_dir = events_dir / "management" / "commands"
    for pkg in (events_dir / "management" / "__init__.py", commands_dir / "__init__.py"):
        if not pkg.exists():
            plan.new[pkg] = ""
            plan.notes.append(f"create: {plan.rel(pkg)}")
    plan.create(commands_dir / "send_event_reminders.py", COMMAND, "send_event_reminders command")

    plan.edit(
        display_py,
        '    "event_reminder": ("calendar-days", "Reminder: {title} is coming up."),\n'
        '    "event_change": ("calendar-days", "{title} was updated."),\n',
        '    "event_reminder": ("calendar-days", "Reminder: {title} starts {lead}."),\n'
        '    "event_changed": ("calendar-days", "{title} has a new time or place."),\n',
        "display: event_reminder text + event_changed slug",
    )
    plan.edit(display_py, "def describe(notification):\n", DISPLAY_LEAD_HELPER, "display: _lead_text helper")
    plan.edit(
        display_py,
        '        title=notification.data.get("title", "An event"),\n',
        '        title=notification.data.get("title", "An event"),\n'
        '        lead=_lead_text(notification.data.get("lead_minutes")),\n',
        "display: pass lead to the template",
    )

    # ---------------------------------------------------- step 4: settings
    plan.replace_file(t_services, THEMING_SERVICES, "REMINDER_MINUTES", "def update_user_settings", "theming/services.py")
    plan.replace_file(t_forms, THEMING_FORMS, "REMINDER_CHOICES", "class UserSettingsForm", "theming/forms.py")
    plan.replace_file(t_views, THEMING_VIEWS, "reminder_initial", "def settings_view", "theming/views.py")

    h = settings_html
    plan.edit(
        h, '{% extends "base_app.html" %}\n',
        '{% extends "base_app.html" %}\n{% load push_tags %}\n',
        "settings.html: load push_tags at the top",
    )
    plan.edit(
        h, '<div class="w-full max-w-2xl">', '<div class="w-full max-w-2xl space-y-6">',
        "settings.html: wrapper spacing",
    )
    plan.edit_regex(
        h, r"^[ \t]*\{% load push_tags %\}[ \t]*\{% push_device_card %\}[ \t]*\n",
        SETTINGS_REMINDER_CARD,
        "settings.html: Event reminders card replaces the push line inside the form",
        done_marker='id="reminder-heading"',
    )
    plan.edit(
        h, SETTINGS_FORM_END_OLD, SETTINGS_FORM_END_NEW,
        "settings.html: push card moves outside the form (nested-form fix)",
    )
    plan.edit(
        push_card,
        "No devices yet. Turn on notifications on a phone or computer and it will appear here.",
        "No devices yet, so event reminders only appear in the bell. "
        "Turn on notifications on a phone or computer and it will appear here.",
        "_push_card.html: no-device prompt",
    )

    # ---------------------------------------------------- tests
    plan.create(events_dir / "tests" / "test_reminders.py", TEST_REMINDERS, "tests: event reminders")
    theming_tests = theming_dir / "tests"
    if theming_tests.is_dir():
        plan.create(theming_tests / "test_reminder_setting.py", TEST_REMINDER_SETTING, "tests: reminder setting")
    elif (theming_dir / "tests.py").is_file():
        plan.warnings.append(
            f"{plan.rel(theming_dir / 'tests.py')} is a single file, not a tests/ folder: "
            "test_reminder_setting.py was NOT created. Move its contents in by hand."
        )
    else:
        plan.new[theming_tests / "__init__.py"] = ""
        plan.create(theming_tests / "test_reminder_setting.py", TEST_REMINDER_SETTING, "tests: reminder setting")

    # existing tests that post the settings form without the new field
    for path in sorted(root.rglob("test*.py")):
        if set(path.parts) & SKIP_DIRS or path.name in ("test_reminders.py", "test_reminder_setting.py"):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "mobile_nav_style" in text and "theming:settings" in text and "event_reminder" not in text:
            plan.warnings.append(
                f'{plan.rel(path)} posts the settings form: add "event_reminder": "1440" to its POST data.'
            )

    report(plan, args.dry_run)


def report(plan, dry_run):
    for note in plan.notes:
        print(f"  {note}")
    for warning in plan.warnings:
        print(f"  WARNING: {warning}")
    if plan.errors:
        print("\nNOTHING WAS WRITTEN. Fix these first:")
        for error in plan.errors:
            print(f"  ERROR: {error}")
        sys.exit(1)
    if dry_run:
        print("\nDry run: nothing written.")
        sys.exit(0)
    plan.write()
    print(
        "\nDone. Next:\n"
        "  python manage.py makemigrations events -n event_reminder\n"
        "  python manage.py makemigrations --check\n"
        "  python manage.py test apps.theming apps.events apps.notifications apps.accounts\n"
        "  make tailwind-build\n"
        "  python manage.py send_event_reminders --dry-run"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
