"""faith.forms: the goal form and the change-schedule form.

Plain Forms (not ModelForms): the services take plain values and validate again with
`services.clean_goal_data`, so the form only gives friendly inline errors.
Dates are Manila dates; times are Manila clock times.
"""
from django import forms
from django.utils import timezone

from apps.core.services import new_submission_token

from . import services
from .models import Goal
from .stats import WEEKDAY_SHORT

WEEKDAY_CHOICES = [(str(i), name) for i, name in enumerate(WEEKDAY_SHORT)]
ENDS_CHOICES = [("never", "Never"), ("date", "On date")]
_DATE_FORMAT = "%Y-%m-%d"


def _date_widget():
    """A browser date picker that sends YYYY-MM-DD."""
    return forms.DateInput(attrs={"type": "date"}, format=_DATE_FORMAT)


def _time_widget():
    """A browser time picker that sends HH:MM."""
    return forms.TimeInput(attrs={"type": "time"}, format="%H:%M")


class GoalForm(forms.Form):
    """Create (goal=None) or edit (goal given) one goal, Google Calendar style.

    When `schedule_locked` is true (the goal already has a check-in) the repeat fields are
    removed and `payload()` takes them from the stored goal instead; the template shows
    them read-only with a Change schedule button.
    """

    title = forms.CharField(label="Title", max_length=services.TITLE_MAX)
    description = forms.CharField(
        label="Description (optional)", required=False, max_length=services.DESCRIPTION_MAX,
        widget=forms.Textarea(attrs={"rows": 3}),
    )
    start_date = forms.DateField(label="Starts", widget=_date_widget())
    all_day = forms.BooleanField(label="All day", required=False)
    start_time = forms.TimeField(label="From", required=False, widget=_time_widget())
    end_time = forms.TimeField(label="To (optional)", required=False, widget=_time_widget())
    repeat_type = forms.ChoiceField(label="Repeat", choices=Goal.Repeat.choices)
    weekdays = forms.MultipleChoiceField(label="On", choices=WEEKDAY_CHOICES, required=False)
    ends = forms.ChoiceField(label="Ends", choices=ENDS_CHOICES)
    end_date = forms.DateField(label="End date", required=False, widget=_date_widget())
    token = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, goal=None, schedule_locked=False, first_checkin=None, **kwargs):
        self.goal = goal
        self.schedule_locked = schedule_locked
        initial = dict(kwargs.pop("initial", None) or {})
        if goal is not None:
            initial.update(
                title=goal.title, description=goal.description, start_date=goal.start_date,
                all_day=goal.start_time is None, start_time=goal.start_time, end_time=goal.end_time,
                repeat_type=goal.repeat_type, weekdays=[str(d) for d in (goal.weekdays or [])],
                ends="date" if goal.end_date else "never", end_date=goal.end_date,
            )
        else:
            initial.setdefault("start_date", timezone.localdate())
            initial.setdefault("all_day", True)
            initial.setdefault("repeat_type", Goal.Repeat.DAILY)
            initial.setdefault("ends", "never")
            initial.setdefault("token", new_submission_token())
        super().__init__(*args, initial=initial, **kwargs)

        if schedule_locked:
            del self.fields["repeat_type"]
            del self.fields["weekdays"]
        if first_checkin is not None:
            self.fields["start_date"].help_text = (
                f"You started checking in on {first_checkin:%b} {first_checkin.day}, "
                "so the start date cannot move later than that."
            )
        for field in self.fields.values():
            if not isinstance(field.widget, (forms.HiddenInput, forms.CheckboxInput)):
                field.widget.attrs.setdefault("class", "input")

    def payload(self, cleaned=None):
        """The plain dict the services expect, built from the cleaned form data."""
        cleaned = cleaned if cleaned is not None else self.cleaned_data
        all_day = bool(cleaned.get("all_day"))
        if self.schedule_locked:
            repeat_type, weekdays = self.goal.repeat_type, self.goal.weekdays
        else:
            repeat_type = cleaned.get("repeat_type")
            weekdays = [int(d) for d in cleaned.get("weekdays") or []]
        return {
            "title": cleaned.get("title"),
            "description": cleaned.get("description"),
            "start_date": cleaned.get("start_date"),
            "end_date": cleaned.get("end_date") if cleaned.get("ends") == "date" else None,
            "start_time": None if all_day else cleaned.get("start_time"),
            "end_time": None if all_day else cleaned.get("end_time"),
            "repeat_type": repeat_type,
            "weekdays": weekdays,
        }

    def clean(self):
        """Friendly inline errors; the service checks everything again."""
        cleaned = super().clean()
        if self.errors:
            return cleaned
        if not cleaned.get("all_day") and not cleaned.get("start_time"):
            self.add_error("start_time", "Pick a start time, or turn on All day.")
        if cleaned.get("ends") == "date" and not cleaned.get("end_date"):
            self.add_error("end_date", "Pick an end date, or choose Never.")
        if self.errors:
            return cleaned
        try:
            services.clean_goal_data(
                self.payload(cleaned), today=timezone.localdate(),
                current_end_date=self.goal.end_date if self.goal else None,
            )
        except services.GoalDataError as exc:
            self.add_error(exc.field if exc.field in self.fields else None, str(exc))
        return cleaned


class ChangeScheduleForm(forms.Form):
    """Pick the new repeat (daily, or weekly with weekdays) for Change schedule."""

    repeat_type = forms.ChoiceField(label="Repeat", choices=Goal.Repeat.choices)
    weekdays = forms.MultipleChoiceField(label="On", choices=WEEKDAY_CHOICES, required=False)

    def clean(self):
        """Weekly needs at least one weekday; the service checks the rest."""
        cleaned = super().clean()
        if cleaned.get("repeat_type") == Goal.Repeat.WEEKLY and not cleaned.get("weekdays"):
            self.add_error("weekdays", "Pick at least one weekday.")
        return cleaned

    def payload(self):
        """The plain dict `services.change_schedule` expects."""
        return {
            "repeat_type": self.cleaned_data["repeat_type"],
            "weekdays": [int(d) for d in self.cleaned_data.get("weekdays") or []],
        }
