"""Forms for managing English landing-page text blocks."""
from django import forms

from apps.core.uploads import validate_photo_upload

from .models import LandingImage


class LandingContentForm(forms.Form):
    """Edit one text block; an empty body resets it to its default."""
    body = forms.CharField(
        required=False,
        strip=False,
        widget=forms.Textarea(attrs={"class": "input", "rows": 12}),
    )


class LandingImageForm(forms.ModelForm):
    """Upload a new landing photo or edit its caption, placement, and image."""
    image = forms.ImageField(
        required=False,
        validators=[validate_photo_upload],
        widget=forms.ClearableFileInput(attrs={"class": "input", "accept": ".jpg,.png,.webp"}),
    )

    class Meta:
        model = LandingImage
        fields = ("image", "caption", "placement")
        widgets = {
            "caption": forms.TextInput(attrs={"class": "input", "maxlength": 255}),
            "placement": forms.Select(attrs={"class": "input"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["image"].required = self.instance.pk is None