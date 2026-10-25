"""Forms for resource management lists and create/edit screens."""
from datetime import date, datetime

from django import forms

from apps.core.uploads import validate_photo_upload, validate_resource_upload
import calendar
from datetime import date

from .models import (
    GeneralResource,
    MonthlyResource,
    MonthlyTheme,
    WeeklyPowerpoint,
    validate_monthly_file,
)

ALLOWED_ACCEPT = ".pdf,.pptx,.docx,.xlsx,.png,.jpg,.jpeg,.webp"
PICTURE_ACCEPT = "image/jpeg,image/png,image/webp"
MAX_PICTURES_PER_UPLOAD = 20


class MultipleFileInput(forms.ClearableFileInput):
    """File input that lets the browser pick several files at once."""
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """FileField that validates every selected file and returns a list."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("widget", MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_clean = super().clean
        if isinstance(data, (list, tuple)):
            if not data:
                single_clean(None, initial)  # raises the "required" error when the field is required
                return []
            return [single_clean(item, initial) for item in data]
        return [single_clean(data, initial)]

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

class MonthYearWidget(forms.MultiWidget):
    """Month and year dropdowns that submit as a single 'YYYY-MM' value."""

    def __init__(self, attrs=None):
        months = [(m, calendar.month_name[m]) for m in range(1, 13)]
        super().__init__(
            [forms.Select(attrs=attrs, choices=months),
             forms.Select(attrs=attrs, choices=[])],
            attrs,
        )

    def decompress(self, value):
        """Split a stored 'YYYY-MM' string (or a date) into [month, year] for display."""
        if isinstance(value, date):
            return [value.month, value.year]
        if value:
            try:
                year, month = str(value).split("-")[:2]
                return [int(month), int(year)]
            except ValueError:
                pass
        today = date.today()
        return [today.month, today.year]

    def get_context(self, name, value, attrs):
        """Build the year list at render time so it never goes stale and always includes the selected year."""
        this_year = date.today().year
        selected_year = self.decompress(value)[1]
        first = min(this_year - 5, selected_year)
        last = max(this_year + 5, selected_year)
        self.widgets[1].choices = [(y, y) for y in range(first, last + 1)]
        return super().get_context(name, value, attrs)

    def value_from_datadict(self, data, files, name):
        """Combine the two selects into 'YYYY-MM' for clean_month."""
        month, year = data.get(f"{name}_0"), data.get(f"{name}_1")
        if month and year and month.isdigit():
            return f"{year}-{int(month):02d}"
        return None


class MonthlyThemeForm(forms.Form):
    """Edit monthly theme text; the month is picked from month/year dropdowns and stored as the 1st of that month."""
    month = forms.CharField(
        label="Month",
        widget=MonthYearWidget(attrs={"class": "input"}),
    )
    title = forms.CharField(
        max_length=255,
        widget=forms.TextInput(attrs={"class": "input"}),
    )
    description = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "input", "rows": 6}),
    )
    cover = forms.FileField(
        required=False,
        label="Cover picture",
        validators=[validate_photo_upload],
        widget=forms.FileInput(attrs={"class": "input", "accept": PICTURE_ACCEPT}),
    )
    remove_cover = forms.BooleanField(required=False, label="Remove the current cover picture")

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
        """Convert the 'YYYY-MM' value from the dropdowns to the canonical first day."""
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

class MonthlyPictureUploadForm(forms.Form):
    """Add several pictures to a month in one go; each becomes its own resource."""
    kind = forms.ChoiceField(
        choices=MonthlyResource.Kind.choices,
        initial=MonthlyResource.Kind.OTHER,
        label="Picture type",
        widget=forms.Select(attrs={"class": "input"}),
    )
    files = MultipleFileField(
        label="Pictures",
        error_messages={"required": "Choose at least one picture."},
        widget=MultipleFileInput(attrs={"class": "input", "accept": PICTURE_ACCEPT}),
    )

    def clean_files(self):
        """Cap how many pictures one upload may carry."""
        files = self.cleaned_data["files"]
        if len(files) > MAX_PICTURES_PER_UPLOAD:
            raise forms.ValidationError(f"Choose up to {MAX_PICTURES_PER_UPLOAD} pictures at a time.")
        return files


class MonthlyResourceForm(_ResourceModelForm):
    """Edit one picture (or older document) attached to a monthly theme."""
    file = forms.FileField(
        required=False,
        validators=[validate_monthly_file],
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
        required=True,
        empty_label="Choose a month",
        label="Month",
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
        self.fields["monthly_theme"].queryset = MonthlyTheme.objects.filter(
            archived_at__isnull=True,
        ).order_by("-month")
        self.fields["monthly_theme"].label_from_instance = lambda theme: f"{theme.month:%B %Y}: {theme.title}"
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