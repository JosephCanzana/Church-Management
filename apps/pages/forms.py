"""Forms for managing English landing-page text blocks."""
from django import forms


class LandingContentForm(forms.Form):
    """Edit one text block; an empty body resets it to its default."""
    body = forms.CharField(
        required=False,
        strip=False,
        widget=forms.Textarea(attrs={"class": "input", "rows": 12}),
    )