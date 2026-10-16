"""theming.forms: input validation for the personal settings page.

Shape checks only (is the value one of the allowed choices). Saving is done by
`theming.services.update_user_settings`, which checks again.
"""
from django import forms

from .models import UserSettings

COLOR_MODE_HELP = {
    UserSettings.ColorMode.LIGHT: "Always use the light colors.",
    UserSettings.ColorMode.DARK: "Always use the dark colors.",
    UserSettings.ColorMode.SYSTEM: "Match your phone or computer, and change when it does.",
}
MOBILE_NAV_HELP = {
    UserSettings.MobileNav.BOTTOM: "A bar at the bottom of the screen with your main links.",
    UserSettings.MobileNav.HAMBURGER: "Open the menu from the button at the top of the screen.",
}


class UserSettingsForm(forms.Form):
    """Color mode and mobile menu style. Both are required: no auto-default on a bad value."""

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

    def options(self, name):
        """The radio cards for one field: [{value, label, help, checked}, ...] for the template."""
        helps = COLOR_MODE_HELP if name == "color_mode" else MOBILE_NAV_HELP
        current = self[name].value()
        return [
            {"value": value, "label": label, "help": helps.get(value, ""), "checked": value == current}
            for value, label in self.fields[name].choices
        ]
