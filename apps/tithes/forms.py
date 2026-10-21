"""tithes.forms: small forms that read and clean what the tithes screens post.

Money and counts are read as TEXT on purpose: `tithes.services` turns them into `Decimal` and refuses a bad
value with a friendly message, so a float never touches an amount. These forms only check that the field is
there, has a sane length and (for ids) is a whole number. Business rules stay in `services.py`.
"""
from django import forms

from apps.accounts.models import Extension

from .services import (
    DENOMINATIONS, MESSAGE_MAX_LENGTH, NAME_MAX_LENGTH, NOTE_MAX_LENGTH, REASON_MAX_LENGTH,
)

INPUT = {"class": "input"}
MONEY = {"class": "input font-mono", "inputmode": "decimal", "autocomplete": "off"}

STATE_CHOICES = [
    ("all", "All sheets"), ("none", "No report yet"), ("draft", "Draft"), ("finalized", "Finalized"),
]


class ReportFilterForm(forms.Form):
    """Filters of the tithes list. The view deletes `extension` (non-admins) and `state` (special members)."""

    state = forms.ChoiceField(choices=STATE_CHOICES, required=False, label="Report", widget=forms.Select(attrs=INPUT))
    extension = forms.ModelChoiceField(
        queryset=Extension.objects.filter(archived_at__isnull=True).order_by("name"),
        required=False, empty_label="All extensions", label="Extension", widget=forms.Select(attrs=INPUT),
    )
    date_from = forms.DateField(required=False, label="From", widget=forms.DateInput(attrs={"type": "date", **INPUT}))
    date_to = forms.DateField(required=False, label="To", widget=forms.DateInput(attrs={"type": "date", **INPUT}))


class OfferingForm(forms.Form):
    """The one offering number. Blank counts as 0."""

    amount = forms.CharField(required=False, max_length=20, widget=forms.TextInput(attrs=MONEY))


class BreakdownForm(forms.Form):
    """Nine count fields, `count_1000` ... `count_1`. A blank field counts as 0."""

    def __init__(self, *args, **kwargs):
        """Add one text field per denomination on the paper form."""
        super().__init__(*args, **kwargs)
        for value in DENOMINATIONS:
            self.fields[f"count_{value}"] = forms.CharField(required=False, max_length=8)

    def counts(self):
        """`{"1000": "3", ...}` as the service expects (text values; the service validates them)."""
        return {str(value): self.cleaned_data.get(f"count_{value}", "") for value in DENOMINATIONS}


class EntryForm(forms.Form):
    """A new envelope: the giver's user id and the amount. Co-givers are read from the repeated `co_giver` field."""

    giver = forms.IntegerField(min_value=1, error_messages={
        "required": "Choose who gave the tithe.", "invalid": "Choose who gave the tithe.",
    })
    amount = forms.CharField(max_length=20, error_messages={"required": "Enter the tithe amount."})


class EntryUpdateForm(forms.Form):
    """Change the amount of one entry."""

    entry_id = forms.IntegerField(min_value=1)
    amount = forms.CharField(max_length=20, error_messages={"required": "Enter the tithe amount."})


class EntryRemoveForm(forms.Form):
    """Take one entry off the draft."""

    entry_id = forms.IntegerField(min_value=1)


class RequestForm(forms.Form):
    """The coordinator's bypass request: a required message and the one-time token."""

    message = forms.CharField(max_length=MESSAGE_MAX_LENGTH, error_messages={
        "required": "Write a message to explain the difference.",
    })
    submit_token = forms.CharField(required=False)


class WithdrawForm(forms.Form):
    """Withdraw one open request."""

    request_id = forms.IntegerField(min_value=1)


class DecisionForm(forms.Form):
    """An admin's decision on a pending request: approve or reject, an optional note, the one-time token."""

    request_id = forms.IntegerField(min_value=1)
    decision = forms.ChoiceField(choices=[("approve", "Approve"), ("reject", "Reject")])
    note = forms.CharField(required=False, max_length=NOTE_MAX_LENGTH)
    submit_token = forms.CharField(required=False)


class FinalizeForm(forms.Form):
    """Finalize: the one-time token and, for an admin only, a reason to finalize a mismatch directly."""

    submit_token = forms.CharField(required=False)
    bypass_reason = forms.CharField(required=False, max_length=REASON_MAX_LENGTH)


class ReopenForm(forms.Form):
    """Reopen a finalized report: just the one-time token."""

    submit_token = forms.CharField(required=False)


class HouseholdForm(forms.Form):
    """A new household: the extension id and an optional name."""

    extension = forms.IntegerField(min_value=1)
    name = forms.CharField(required=False, max_length=NAME_MAX_LENGTH)


class HouseholdMemberForm(forms.Form):
    """Put a person in a household: their user id and an optional start date (default today)."""

    user = forms.IntegerField(min_value=1, error_messages={"required": "Choose a person to add."})
    started_on = forms.DateField(required=False)
