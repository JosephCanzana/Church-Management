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

from django.db.models import Q

from .models import Extension, Role, Status, User
from .permissions import assignable_roles

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


# ================================================================== people
class UserForm(forms.Form):
    """Details, role and extension of a person. Base of the create and edit forms.

    actor           -- decides which roles appear in the role list.
    keep_extension  -- id of the extension the person is already in, so it stays
                       selectable even if that extension has since been archived.
    The role decides the extension: coordinator and member need one; admin and
    super-admin never have one (the choice is dropped).
    """

    first_name = forms.CharField(max_length=150, strip=True, widget=forms.TextInput(attrs={"class": "input"}))
    middle_name = forms.CharField(
        max_length=150, strip=True, required=False, widget=forms.TextInput(attrs={"class": "input"}),
    )
    last_name = forms.CharField(max_length=150, strip=True, widget=forms.TextInput(attrs={"class": "input"}))
    birth_date = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={"class": "input", "type": "date"}, format="%Y-%m-%d"),
    )
    role = forms.ChoiceField(widget=forms.Select(attrs={"class": "input", "x-model": "role"}))
    extension = forms.ModelChoiceField(
        queryset=Extension.objects.none(), required=False, empty_label="Choose an extension",
        widget=forms.Select(attrs={"class": "input"}),
    )

    def __init__(self, *args, actor, keep_extension=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["role"].choices = [(r, Role(r).label) for r in assignable_roles(actor)]
        allowed = Q(archived_at__isnull=True)
        if keep_extension:
            allowed |= Q(pk=keep_extension)
        self.fields["extension"].queryset = Extension.objects.filter(allowed).order_by("name")

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        if role in (Role.COORDINATOR, Role.MEMBER):
            if cleaned.get("extension") is None and "extension" not in self.errors:
                self.add_error("extension", "Choose an extension for this role.")
        elif role:
            cleaned["extension"] = None
        return cleaned


class UserCreateForm(UserForm):
    """New person. A blank password means: use the default, or generate one."""

    password = forms.CharField(
        required=False, strip=False, label="Password",
        widget=forms.PasswordInput(attrs={"class": "input", "autocomplete": "new-password"}, render_value=False),
        help_text="Leave empty to use the default password, or to generate one that is shown once.",
    )
    activated = forms.BooleanField(
        required=False, label="Skip activation",
        widget=forms.CheckboxInput(attrs={"class": "mt-0.5 size-4 accent-accent"}),
        help_text="The account is active at once and is not asked to change the password. Meant for test accounts.",
    )

    def clean_password(self):
        value = self.cleaned_data.get("password") or ""
        if value and len(value) < 8:
            raise ValidationError("Use at least 8 characters, or leave it empty.")
        return value


class UserEditForm(UserForm):
    """Existing person. Passwords are changed with Reset password, not here."""


class UserFilterForm(forms.Form):
    """Search, filters and sort for the people list (GET parameters)."""

    STATUS_CHOICES = [
        ("current", "Not archived"),
        ("not_activated", "Not activated"),
        ("active", "Active"),
        ("suspended", "Suspended"),
        ("archived", "Archived"),
        ("all", "All"),
    ]
    ROLE_CHOICES = [("", "All roles"), *Role.choices]
    SORT_CHOICES = [
        ("last_name", "Last name, A to Z"),
        ("-last_name", "Last name, Z to A"),
        ("first_name", "First name, A to Z"),
        ("-first_name", "First name, Z to A"),
        ("account_id", "Account ID, lowest first"),
        ("-account_id", "Account ID, highest first"),
    ]

    q = forms.CharField(
        required=False, strip=True, max_length=100, label="Search",
        widget=forms.TextInput(attrs={"class": "input", "placeholder": "Name or account ID"}),
    )
    status = forms.ChoiceField(
        required=False, choices=STATUS_CHOICES, label="Status",
        widget=forms.Select(attrs={"class": "input"}),
    )
    role = forms.ChoiceField(
        required=False, choices=ROLE_CHOICES, label="Role",
        widget=forms.Select(attrs={"class": "input"}),
    )
    extension = forms.ModelChoiceField(
        queryset=Extension.objects.order_by("name"), required=False, empty_label="All extensions",
        widget=forms.Select(attrs={"class": "input"}),
    )
    sort = forms.ChoiceField(
        required=False, choices=SORT_CHOICES, label="Sort by",
        widget=forms.Select(attrs={"class": "input"}),
    )
