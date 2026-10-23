"""prayer.forms: the send form and the reply form. Text limits match `services.MAX_BODY`."""
from django import forms

from .services import MAX_BODY


class PrayerSendForm(forms.Form):
    """A member's new prayer request: the message and the anonymous switch."""

    body = forms.CharField(
        label="Your prayer request", max_length=MAX_BODY, strip=True,
        widget=forms.Textarea(attrs={"rows": 5, "class": "input", "maxlength": MAX_BODY}),
        error_messages={"required": "Please write your prayer request."},
    )
    is_anonymous = forms.BooleanField(
        label="Send anonymously", required=False,
        help_text="Nobody will see your name, including your coordinator.",
    )


class PrayerReplyForm(forms.Form):
    """A coordinator's or admin's reply to one request."""

    body = forms.CharField(
        label="Your reply", max_length=MAX_BODY, strip=True,
        widget=forms.Textarea(attrs={"rows": 4, "class": "input", "maxlength": MAX_BODY}),
        error_messages={"required": "Please write a reply."},
    )
