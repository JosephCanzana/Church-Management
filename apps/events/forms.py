"""events.forms: the create / edit form.

A plain Form (not a ModelForm) because the services take keyword arguments and do the real
validation again. The form only gives friendly inline errors and the right choices.

Who is it for: admins get "Everyone (global event)" plus every live extension; a coordinator
gets only their own extension. The scope is chosen at creation and cannot change afterwards,
so the edit form has no such field.
"""
from django import forms
from django.utils import timezone

from apps.accounts.models import Extension
from apps.core.services import new_submission_token
from apps.core.text import title_case

from . import permissions
from .services import LOCATION_MAX, TITLE_MAX

_DATETIME_FORMAT = "%Y-%m-%dT%H:%M"      # what <input type="datetime-local"> sends


class _ExtensionChoiceField(forms.ModelChoiceField):
    """Extension dropdown that shows names the way the rest of the app does (title case)."""

    def label_from_instance(self, obj):
        return title_case(obj.name)


def _datetime_field(label, required, help_text=""):
    """A Manila-time date and time field (a naive value is read in the current time zone)."""
    return forms.DateTimeField(
        label=label, required=required, help_text=help_text,
        input_formats=[_DATETIME_FORMAT, "%Y-%m-%dT%H:%M:%S"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format=_DATETIME_FORMAT),
    )


def _same_minute(new, old):
    """Keep the stored value when the browser's minute-precision value is the same minute.

    The browser never sends seconds, so an event stored with seconds would otherwise look
    "changed" on every save (and could trigger a time-change notice by mistake).
    """
    if new is not None and old is not None and new == old.replace(second=0, microsecond=0):
        return old
    return new


class EventForm(forms.Form):
    """Create (event=None) or edit (event given) one event."""

    extension = _ExtensionChoiceField(queryset=Extension.objects.none(), required=False, label="Who is it for?")
    title = forms.CharField(label="Title", max_length=TITLE_MAX)
    start_at = _datetime_field("Starts", required=True)
    end_at = _datetime_field("Ends (optional)", required=False)
    location = forms.CharField(label="Location (optional)", max_length=LOCATION_MAX, required=False)
    description = forms.CharField(label="Description (optional)", required=False, widget=forms.Textarea(attrs={"rows": 5}))
    token = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, actor, event=None, **kwargs):
        self.actor = actor
        self.event = event
        initial = dict(kwargs.pop("initial", None) or {})
        if event is not None:
            initial.update(
                title=event.title, start_at=event.start_at, end_at=event.end_at,
                location=event.location, description=event.description,
            )
        else:
            initial.setdefault("token", new_submission_token())
        super().__init__(*args, initial=initial, **kwargs)

        if event is not None:
            del self.fields["extension"]          # the scope cannot change after creation
            if event.start_at <= timezone.now():
                # Locked on purpose: the service refuses to move a start that already passed.
                self.fields["start_at"].disabled = True
                self.fields["start_at"].help_text = "This event already started, so its start cannot be moved."
        else:
            self._setup_extension_field()

        for name, field_ in self.fields.items():
            if not isinstance(field_.widget, forms.HiddenInput):
                field_.widget.attrs.setdefault("class", "input")

    def _setup_extension_field(self):
        """Offer only the scopes this person may create for (the service checks again)."""
        field_ = self.fields["extension"]
        field_.queryset = permissions.creatable_extensions(self.actor).order_by("name")
        if permissions.can_create_global(self.actor):
            field_.empty_label = "Everyone (global event)"
            field_.required = False
            field_.help_text = "A global event is shown to every extension."
        else:
            field_.empty_label = None
            field_.required = True

    # ------------------------------------------------------------- cleaning
    def clean_start_at(self):
        value = self.cleaned_data.get("start_at")
        return _same_minute(value, self.event.start_at) if self.event else value

    def clean_end_at(self):
        value = self.cleaned_data.get("end_at")
        return _same_minute(value, self.event.end_at) if self.event else value

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_at"), cleaned.get("end_at")
        if start and end and end <= start:
            self.add_error("end_at", "The end must be after the start.")
        return cleaned

    @property
    def extension_id(self):
        """The chosen extension's id, or None for a global event (create only)."""
        extension = self.cleaned_data.get("extension")
        return extension.pk if extension else None
