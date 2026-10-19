"""Forms for resource management lists and create/edit screens."""
from datetime import date, datetime

from django import forms

from apps.core.uploads import validate_resource_upload

from .models import GeneralResource, MonthlyResource, MonthlyTheme, WeeklyPowerpoint

ALLOWED_ACCEPT = ".pdf,.pptx,.docx,.xlsx,.png,.jpg,.jpeg,.webp"

class ResourceFilterForm(forms.Form):
    """Search, status, and sort controls shared by resource management lists."""
    STATUS_CHOICES = (("active", "Active"), ("archived", "Archived"), ("all", "All"))

    q = forms.CharField(
        required=False,
        strip=True,
        max_length=100,
        label="Search",
        widget=forms.TextInput(attrs={"class": "input", "placeholder": "Search by title"}),
    )
    status = forms.ChoiceField(
        required=False,
        choices=STATUS_CHOICES,
        initial="active",
        widget=forms.Select(attrs={"class": "input"}),
    )

    def __init__(self, *args, sort_choices, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["sort"] = forms.ChoiceField(
            required=False,
            choices=sort_choices,
            initial=sort_choices[0][0],
            label="Sort by",
            widget=forms.Select(attrs={"class": "input"}),
        )


class MonthlyThemeForm(forms.Form):
    """Edit monthly theme text while accepting a month-only date value."""
    month = forms.CharField(
        label="Month",
        widget=forms.TextInput(attrs={"class": "input", "type": "month"}),
    )
    title = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "input", "rows": 6}),
    )

    def __init__(self, *args, instance=None, **kwargs):
        if instance is not None and "initial" not in kwargs:
            kwargs["initial"] = {
                "month": instance.month.strftime("%Y-%m"),
                "title": instance.title,
                "description": instance.description,
            }
        super().__init__(*args, **kwargs)
        self.instance = instance

    def clean_month(self):
        """Convert the browser's YYYY-MM value to the canonical first day."""
        value = self.cleaned_data["month"]
        try:
            parsed = datetime.strptime(value, "%Y-%m").date()
        except ValueError as error:
            raise forms.ValidationError("Enter a valid month.") from error
        return date(parsed.year, parsed.month, 1)


class _ResourceModelForm(forms.ModelForm):
    """Apply shared token styling to all rendered model fields."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            existing = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing} input".strip()

class MonthlyResourceForm(_ResourceModelForm):
    """Edit one optional file attached to a monthly theme."""
    file = forms.FileField(
        required=False,
        validators=[validate_resource_upload],
        widget=forms.FileInput(attrs={
            "class": "input",
            "accept": ALLOWED_ACCEPT,
        }),
    )

    class Meta:
        model = MonthlyResource
        fields = ("kind", "title", "description", "file")
        widgets = {"description": forms.Textarea(attrs={"rows": 5})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["file"].required = self.instance.pk is None

class WeeklyPowerpointForm(_ResourceModelForm):
    """Create or edit a weekly presentation and its optional theme link."""
    file = forms.FileField(
        required=False,
        validators=[validate_resource_upload],
        widget=forms.ClearableFileInput(attrs={"class": "input"}),
    )
    monthly_theme = forms.ModelChoiceField(
        queryset=MonthlyTheme.objects.none(),
        required=False,
        empty_label="No theme",
        label="Monthly theme",
    )

    class Meta:
        model = WeeklyPowerpoint
        fields = ("title", "description", "week_start", "monthly_theme", "file")
        widgets = {
            "description": forms.Textarea(attrs={"rows": 5}),
            "week_start": forms.DateInput(attrs={"type": "date"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["monthly_theme"].queryset = MonthlyTheme.objects.filter(archived_at__isnull=True)
        self.fields["file"].required = self.instance.pk is None


class GeneralResourceForm(_ResourceModelForm):
    """Create or edit a general resource document."""
    file = forms.FileField(
        required=False,
        validators=[validate_resource_upload],
        widget=forms.ClearableFileInput(attrs={"class": "input"}),
    )

    class Meta:
        model = GeneralResource
        fields = ("title", "description", "category", "file")
        widgets = {"description": forms.Textarea(attrs={"rows": 5})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["file"].required = self.instance.pk is None