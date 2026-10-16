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
