"""Forms for managing English landing-page text blocks."""
from django import forms
from django.core.exceptions import ValidationError

from apps.core.uploads import validate_photo_upload

from .models import DonationAccount, LandingImage
from .site_settings import validate_footer_url


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


class DonationAccountForm(forms.ModelForm):
    """Edit public donation details and an optional QR image."""
    class Meta:
        model = DonationAccount
        fields = (
            "payment_app", "account_name", "account_number", "swift_code",
            "instructions", "qr_image", "sort_order",
        )
        widgets = {
            "payment_app": forms.TextInput(attrs={"class": "input"}),
            "account_name": forms.TextInput(attrs={"class": "input"}),
            "account_number": forms.TextInput(attrs={"class": "input", "inputmode": "text"}),
            "swift_code": forms.TextInput(attrs={"class": "input", "autocapitalize": "characters"}),
            "instructions": forms.Textarea(attrs={"class": "input", "rows": 5}),
            "qr_image": forms.ClearableFileInput(attrs={"class": "input", "accept": ".jpg,.png,.webp"}),
            "sort_order": forms.NumberInput(attrs={"class": "input", "min": 0}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["payment_app"].label = "Payment method or bank"
        self.fields["sort_order"].required = False
        self.fields["qr_image"].required = False

    def clean_sort_order(self):
        """Treat an empty order as the end of the account list."""
        return self.cleaned_data.get("sort_order") or 0


class FooterLinkForm(forms.Form):
    """Create or edit one footer link with an allowlisted URL scheme."""
    label = forms.CharField(
        max_length=100,
        widget=forms.TextInput(attrs={"class": "input", "maxlength": 100}),
    )
    url = forms.CharField(
        max_length=500,
        widget=forms.TextInput(attrs={"class": "input", "maxlength": 500}),
    )

    def clean_url(self):
        """Accept only the site's supported external and relative URL forms."""
        try:
            return validate_footer_url(self.cleaned_data["url"])
        except ValidationError as error:
            raise forms.ValidationError(error.messages) from error