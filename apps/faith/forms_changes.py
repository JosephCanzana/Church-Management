"""faith.forms_changes: the two forms for changing a goal that has already started.

* ``EndDateForm``: a started goal with an end date can only have that end date changed.
* ``ChangeScheduleForm``: a started goal with no end date changes how it repeats and its time of day.
The services check every rule again; these forms only read and shape the input.
"""
from django import forms

REPEAT_CHOICES = [("daily", "Daily"), ("weekly", "Weekly")]  # same values as Goal.Repeat
WEEKDAY_CHOICES = [("0", "Mon"), ("1", "Tue"), ("2", "Wed"), ("3", "Thu"), ("4", "Fri"), ("5", "Sat"), ("6", "Sun")]


def _time_widget():
    """A 24-hour time picker styled like the other inputs."""
    return forms.TimeInput(attrs={"type": "time", "class": "input"}, format="%H:%M")


class EndDateForm(forms.Form):
    """Only the end date of a started task."""

    end_date = forms.DateField(
        label="End date",
        widget=forms.DateInput(attrs={"type": "date", "class": "input"}, format="%Y-%m-%d"),
    )

    def payload(self):
        """Return the clean end date."""
        return self.cleaned_data["end_date"]


class ChangeScheduleForm(forms.Form):
    """New repeat (daily, or weekly on chosen days) and new time of day (all day, or a start and end time)."""

    repeat_type = forms.ChoiceField(label="New repeat", choices=REPEAT_CHOICES, widget=forms.RadioSelect)
    weekdays = forms.MultipleChoiceField(
        label="On these days", choices=WEEKDAY_CHOICES, required=False, widget=forms.CheckboxSelectMultiple,
    )
    all_day = forms.BooleanField(label="All day", required=False)
    start_time = forms.TimeField(label="Starts at", required=False, widget=_time_widget())
    end_time = forms.TimeField(label="Ends at (optional)", required=False, widget=_time_widget())

    def clean(self):
        """All day clears the times; otherwise a start time is needed and the end must be after it."""
        data = super().clean()
        if data.get("all_day"):
            data["start_time"] = data["end_time"] = None
            return data
        start, end = data.get("start_time"), data.get("end_time")
        if not start:
            self.add_error("start_time", "Set a start time, or choose All day.")
        elif end and end <= start:
            self.add_error("end_time", "The end time must be after the start time. A goal cannot run past midnight.")
        return data

    def payload(self):
        """Return what ``services.change_schedule`` expects (weekdays as integers, times or None)."""
        data = self.cleaned_data
        return {
            "repeat_type": data["repeat_type"],
            "weekdays": [int(day) for day in data.get("weekdays") or []],
            "start_time": data.get("start_time"),
            "end_time": data.get("end_time"),
        }
