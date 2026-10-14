"""attendance.forms: the forms for the sheet screens (brick 8).

`SheetCreateForm` starts a draft sheet; `SheetFilterForm` narrows the sheet list; `FirstTimerForm` is the
quick first-timer form of the draft workspace. Forms only check the
SHAPE of what was typed. Who may create what, and the "same extension, date and label" refusal, belong to
`services.create_sheet`, which checks again whatever the browser posted.
"""
from django import forms
from django.utils import timezone

from apps.accounts.models import Extension, Gender
from apps.core.text import title_case

from .age import AgeNetwork

DEFAULT_LABEL = "Sunday Service"


class ExtensionChoiceField(forms.ModelChoiceField):
    """An extension picker that shows names in title case (they are stored lowercase)."""

    def label_from_instance(self, obj):
        return title_case(obj.name)


def _date_input():
    """A native date picker (the browser draws it; the value is always YYYY-MM-DD)."""
    return forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d")


class _StyledForm(forms.Form):
    """Gives every visible field the app's `.input` class, and `.input-error` once it has an error."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not field.widget.is_hidden and not isinstance(field.widget, forms.RadioSelect):
                field.widget.attrs.setdefault("class", "input")

    def full_clean(self):
        """Validate, then mark the widgets of failed fields so the template needs no per-field logic."""
        super().full_clean()
        for name in self.errors:
            widget = self.fields[name].widget if name in self.fields else None
            if widget is not None and not widget.is_hidden and not isinstance(widget, forms.RadioSelect):
                self.fields[name].widget.attrs["class"] = "input input-error"
                self.fields[name].widget.attrs["aria-invalid"] = "true"


class SheetCreateForm(_StyledForm):
    """Date, label and (when there is a choice) extension for a new draft sheet.

    `extensions` is the queryset of extensions the person may create for
    (`permissions.creatable_extensions`). With exactly one, the field becomes hidden and the template shows
    the name instead of a select. The posted value is still checked against the queryset, so a changed
    hidden id is refused.
    """

    extension = ExtensionChoiceField(queryset=Extension.objects.none(), empty_label="Choose an extension")
    attendance_date = forms.DateField(label="Date", widget=_date_input(), initial=timezone.localdate)
    label = forms.CharField(label="Label", max_length=100, initial=DEFAULT_LABEL)

    def __init__(self, *args, extensions, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields["extension"]
        field.queryset = extensions.order_by("name")
        only = list(field.queryset[:2])
        self.single_extension = only[0] if len(only) == 1 else None
        if self.single_extension:
            # One choice: nothing to pick. The field stays (hidden) so the same code path validates it.
            field.widget = forms.HiddenInput()
            field.required = False
            self.initial["extension"] = self.single_extension.pk

    def clean_extension(self):
        """Fall back to the only allowed extension when the hidden field was left out of the post."""
        return self.cleaned_data.get("extension") or self.single_extension

    def clean_label(self):
        """Trim and collapse spaces so "Sunday  Service " and "Sunday Service" are the same label."""
        return " ".join(self.cleaned_data["label"].split())


class SheetFilterForm(_StyledForm):
    """Filters for the sheet list: status, extension (admin only), and a date range.

    The view removes `status` and `extension` for roles that do not get them.
    """

    STATUS_CHOICES = [
        ("current", "Draft and uploaded"),
        ("draft", "Draft only"),
        ("uploaded", "Uploaded only"),
        ("archived", "Archived"),
        ("all", "All"),
    ]

    status = forms.ChoiceField(label="Status", choices=STATUS_CHOICES, required=False)
    extension = ExtensionChoiceField(
        label="Extension", queryset=Extension.objects.order_by("name"), required=False, empty_label="All extensions",
    )
    date_from = forms.DateField(label="From", required=False, widget=_date_input())
    date_to = forms.DateField(label="To", required=False, widget=_date_input())

    def clean(self):
        """A range that ends before it starts is an error on the end date; the view then ignores that date."""
        data = super().clean()
        start, end = data.get("date_from"), data.get("date_to")
        if start and end and end < start:
            self.add_error("date_to", "This date is before the start date.")
        return data


class FirstTimerForm(_StyledForm):
    """The first-timer form of the draft workspace: four quick fields and a "more" fold.

    Gender and age network are one tap each (the template draws the radios itself, big enough for a thumb).
    A birth date, when given, decides the age network in the service, so the network is only required
    when there is no birth date. `confirm_new` is posted by the "same person?" prompt (answer: a new person).
    Only the SHAPE is checked here; names, phone and the duplicate rules belong to `services.add_first_timer`.
    """

    #: Fields behind the "More details" fold.
    MORE_FIELDS = ("middle_name", "suffix", "birth_date", "phone_number")
    #: Every field the "same person?" prompt posts back (so the taker's typing is not lost).
    REPOST_FIELDS = ("first_name", "last_name", "gender", "age_network", *MORE_FIELDS)

    first_name = forms.CharField(
        label="First name", max_length=150, widget=forms.TextInput(attrs={"autocomplete": "off", "autocapitalize": "words"}),
    )
    last_name = forms.CharField(
        label="Last name", max_length=150, widget=forms.TextInput(attrs={"autocomplete": "off", "autocapitalize": "words"}),
    )
    gender = forms.ChoiceField(
        label="Gender", choices=Gender.choices, widget=forms.RadioSelect,
        error_messages={"required": "Choose a gender."},
    )
    age_network = forms.ChoiceField(
        label="Age network", choices=AgeNetwork.choices, widget=forms.RadioSelect, required=False,
    )
    middle_name = forms.CharField(label="Middle name", max_length=150, required=False)
    suffix = forms.CharField(label="Suffix", max_length=10, required=False)
    birth_date = forms.DateField(label="Birth date", required=False, widget=_date_input())
    phone_number = forms.CharField(
        label="Phone number", max_length=30, required=False, widget=forms.TextInput(attrs={"inputmode": "tel"}),
    )
    confirm_new = forms.BooleanField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        """Make every text-like field 16px (`.input` is smaller; iOS Safari zooms into inputs under 16px)."""
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if not widget.is_hidden and not isinstance(widget, forms.RadioSelect):
                widget.attrs["class"] = f"{widget.attrs.get('class', 'input')} text-base"

    def clean(self):
        """The age network is needed unless a birth date gives it."""
        data = super().clean()
        if not data.get("birth_date") and not data.get("age_network") and "age_network" not in self.errors:
            self.add_error("age_network", "Choose an age network, or give a birth date.")
        return data
