"""accounts.forms: input validation for the accounts app.

Forms only check the SHAPE of the input (is it empty, does it look like an
account id or an email, is the name taken). Whether a password is actually
correct is decided by the backend in `accounts/backends.py`, and every
business rule lives in `accounts/services.py`.
"""
import re

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from .models import Extension, Role, Status, User

# One message for every login failure, so the page never reveals whether the
# account exists, the password was wrong, or the account is archived.
GENERIC_LOGIN_ERROR = "The account ID or password is incorrect."

# Same rule as the `app_user_account_id_format` database constraint.
ACCOUNT_ID_PATTERN = re.compile(r"^[1-9][0-9]{7,}$")


# ============================================================ login
class LoginForm(forms.Form):
    """Account id (or verified email) plus password."""

    identifier = forms.CharField(max_length=254, strip=True)
    # strip=False: spaces at the start or end may be part of a password.
    password = forms.CharField(strip=False, widget=forms.PasswordInput)

    def clean_identifier(self):
        """Accept only something shaped like an account id or an email.

        A badly shaped value gets the SAME generic message as a wrong
        password, so the form never gives extra hints about what is valid.
        """
        value = self.cleaned_data["identifier"]
        if "@" in value:
            try:
                validate_email(value)
            except ValidationError:
                raise ValidationError(GENERIC_LOGIN_ERROR)
        elif not ACCOUNT_ID_PATTERN.match(value):
            raise ValidationError(GENERIC_LOGIN_ERROR)
        return value


# ============================================================ extensions
class ExtensionForm(forms.ModelForm):
    """Create or edit an extension. The view passes `cleaned_data` to the service.

    Invalid fields get `aria-invalid` from Django, which input.css styles.
    """

    class Meta:
        model = Extension
        fields = [
            "name", "building_number", "street", "barangay",
            "municipality", "province", "country", "postal_code",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input", "autocomplete": "off"}),
            "building_number": forms.TextInput(attrs={"class": "input"}),
            "street": forms.TextInput(attrs={"class": "input"}),
            "barangay": forms.TextInput(attrs={"class": "input"}),
            "municipality": forms.TextInput(attrs={"class": "input"}),
            "province": forms.TextInput(attrs={"class": "input"}),
            "country": forms.TextInput(attrs={"class": "input"}),
            "postal_code": forms.TextInput(attrs={"class": "input font-mono"}),
        }
        labels = {"name": "Extension name"}

    def clean_name(self):
        """Collapse extra spaces and reject a name used by another extension.

        The database unique index is case-sensitive, so "Cabanatuan" and
        "cabanatuan" would both pass it; this check closes that gap.
        """
        name = " ".join(self.cleaned_data["name"].split())
        taken = Extension.objects.filter(name__iexact=name)
        if self.instance.pk:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise ValidationError("An extension with this name already exists.")
        return name


class ExtensionFilterForm(forms.Form):
    """Search, status filter and sort for the extension list (GET parameters)."""

    STATUS_CHOICES = [("active", "Active"), ("archived", "Archived"), ("all", "All")]
    SORT_CHOICES = [
        ("name", "Name, A to Z"),
        ("-name", "Name, Z to A"),
        ("-created_at", "Newest first"),
        ("created_at", "Oldest first"),
    ]

    q = forms.CharField(
        required=False, strip=True, max_length=100, label="Search",
        widget=forms.TextInput(attrs={"class": "input", "placeholder": "Name, barangay or municipality"}),
    )
    status = forms.ChoiceField(
        required=False, choices=STATUS_CHOICES,
        widget=forms.Select(attrs={"class": "input"}),
    )
    sort = forms.ChoiceField(
        required=False, choices=SORT_CHOICES, label="Sort by",
        widget=forms.Select(attrs={"class": "input"}),
    )


class CoordinatorChoiceField(forms.ModelChoiceField):
    """Shows people as 'Full name (account id)'."""

    def label_from_instance(self, obj):
        return f"{obj.full_name} ({obj.account_id})"


class AssignCoordinatorForm(forms.Form):
    """Pick the coordinator from the members of one extension."""

    user = CoordinatorChoiceField(
        queryset=User.objects.none(),
        empty_label="Choose a member",
        label="New coordinator",
        widget=forms.Select(attrs={"class": "input"}),
    )

    def __init__(self, *args, extension, **kwargs):
        super().__init__(*args, **kwargs)
        people = (
            User.objects.filter(extension=extension, role__in=[Role.MEMBER, Role.COORDINATOR])
            .exclude(status=Status.ARCHIVED)
            .order_by("last_name", "first_name", "pk")
        )
        if extension.coordinator_id:
            people = people.exclude(pk=extension.coordinator_id)
        self.fields["user"].queryset = people
