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

from apps.core.text import clean_text, title_case

from .address import COUNTRIES, HIERARCHY, PHILIPPINES, canonical_country, check_address
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

    - Fields run from the widest area to the smallest: country, province,
      municipality, barangay, postal code, street, building number.
    - Every field is required except the building number.
    - Input is cleaned to lowercase with single spaces (`core.text.clean_text`);
      the form shows saved values back in title case.
    - For the Philippines the province, municipality and barangay must come
      from `accounts.address` (the template turns them into cascading
      dropdowns, this class is the server-side check).
    - Invalid fields get `aria-invalid` from Django, which input.css styles.
    """

    #: Order of the fields (the template draws them by hand in this order).
    field_order = [
        "name", "country", "province", "municipality", "barangay",
        "postal_code", "street", "building_number",
    ]
    #: The only field that may stay empty.
    OPTIONAL_FIELDS = {"building_number"}
    #: Fields shown back in title case when an extension is edited.
    TITLE_FIELDS = ("name", "province", "municipality", "barangay", "street", "building_number")

    # Declared here (not just in Meta) because it is a dropdown, not free text.
    # The initial value is the model default for a new extension.
    country = forms.ChoiceField(label="Country", initial=PHILIPPINES, choices=[])

    class Meta:
        model = Extension
        fields = [
            "name", "country", "province", "municipality", "barangay",
            "postal_code", "street", "building_number",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input", "autocomplete": "off"}),
            "province": forms.TextInput(attrs={"class": "input", "autocomplete": "off"}),
            "municipality": forms.TextInput(attrs={"class": "input", "autocomplete": "off"}),
            "barangay": forms.TextInput(attrs={"class": "input", "autocomplete": "off"}),
            "postal_code": forms.TextInput(attrs={
                "class": "input font-mono", "maxlength": "10", "inputmode": "numeric",
                "autocomplete": "off",
            }),
            "street": forms.TextInput(attrs={"class": "input", "autocomplete": "off"}),
            "building_number": forms.TextInput(attrs={"class": "input", "autocomplete": "off"}),
        }
        labels = {"name": "Extension name"}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            field.required = name not in self.OPTIONAL_FIELDS

        choices = [("", "Choose a country")] + [(c, c) for c in COUNTRIES]
        saved_country = self.instance.country if self.instance.pk else ""
        if saved_country and not canonical_country(saved_country):
            # An old value that is not in the list stays selectable until changed.
            old = title_case(saved_country)
            choices.append((old, old))
        self.fields["country"].choices = choices

        if self.instance.pk and not self.is_bound:
            # Saved values are lowercase (or old mixed case); show them as titles.
            for name in self.TITLE_FIELDS:
                self.initial[name] = title_case(getattr(self.instance, name))
            self.initial["country"] = canonical_country(saved_country) or title_case(saved_country)

    @property
    def address_state(self):
        """Current country/province/municipality/barangay for the page's JavaScript."""
        return {name: self[name].value() or "" for name in HIERARCHY}

    # --- cleaning: everything stored lowercase ---------------------------
    def _lowered(self, name):
        return clean_text(self.cleaned_data.get(name))

    def clean_country(self):
        return self._lowered("country")

    def clean_province(self):
        return self._lowered("province")

    def clean_municipality(self):
        return self._lowered("municipality")

    def clean_barangay(self):
        return self._lowered("barangay")

    def clean_street(self):
        return self._lowered("street")

    def clean_building_number(self):
        return self._lowered("building_number")

    def clean_postal_code(self):
        """Single spaces only; the case is left alone (foreign codes like 'SW1A 1AA')."""
        return " ".join((self.cleaned_data.get("postal_code") or "").split())

    def clean_name(self):
        """Lowercase, single spaces, and not used by another extension.

        The database unique index is case-sensitive, so "Cabanatuan" and
        "cabanatuan" would both pass it; this check closes that gap.
        """
        name = self._lowered("name")
        taken = Extension.objects.filter(name__iexact=name)
        if self.instance.pk:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise ValidationError("An extension with this name already exists.")
        return name

    def clean(self):
        """Apply the Philippine address rules (same function the service uses)."""
        cleaned = super().clean()
        existing = None
        if self.instance.pk:
            # clean() runs before the instance is updated, so these are the saved values.
            existing = {name: getattr(self.instance, name) for name in self.fields}
        for field, message in check_address(cleaned, existing).items():
            if field in self.fields and field not in self.errors:
                self.add_error(field, message)
        return cleaned


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
    """Details, role and extension fields shared by person create and edit forms.

    actor           -- decides which roles appear in the role list.
    keep_role       -- the person's current role when it cannot be handed out (a
                       super-admin): it is shown, read-only, and kept.
    exclude_pk      -- the person being edited, left out of the duplicate-name check.
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

    def __init__(self, *args, actor, keep_extension=None, keep_role=None, exclude_pk=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.exclude_pk = exclude_pk
        roles = list(assignable_roles(actor))
        if keep_role and keep_role not in roles:
            roles.insert(0, keep_role)
            self.fields["role"].disabled = True     # the value comes from `initial`
        self.fields["role"].choices = [(r, Role(r).label) for r in roles]
        allowed = Q(archived_at__isnull=True)
        if keep_extension:
            allowed |= Q(pk=keep_extension)
        self.fields["extension"].queryset = Extension.objects.filter(allowed).order_by("name")

    # Names are stored lowercase with single spaces; the pages show them in name case.
    def clean_first_name(self):
        return clean_text(self.cleaned_data.get("first_name"))

    def clean_middle_name(self):
        return clean_text(self.cleaned_data.get("middle_name"))

    def clean_last_name(self):
        return clean_text(self.cleaned_data.get("last_name"))

    def clean(self):
        cleaned = super().clean()
        role = cleaned.get("role")
        if role in (Role.COORDINATOR, Role.MEMBER):
            if cleaned.get("extension") is None and "extension" not in self.errors:
                self.add_error("extension", "Choose an extension for this role.")
        elif role:
            cleaned["extension"] = None

        names_ok = cleaned.get("first_name") and cleaned.get("last_name") and not any(
            n in self.errors for n in ("first_name", "middle_name", "last_name", "birth_date")
        )
        if names_ok:
            # Imported here: the services import the models, never the forms.
            from .services.users import duplicate_message
            message = duplicate_message(cleaned, exclude_pk=self.exclude_pk)
            if message:
                self.add_error(None, message)
        return cleaned


class UserCreateForm(UserForm):
    """New person. A blank password means: use the default, or generate one."""

    password = forms.CharField(
        required=False, strip=False, label="Password",
        widget=forms.PasswordInput(attrs={"class": "input", "autocomplete": "new-password"}, render_value=False),
        help_text="Leave empty to use your default password for this role, or a generated one shown once.",
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


class DefaultPasswordsForm(forms.Form):
    """The signed-in person's own default passwords: one optional password and one Remove tick per role.

    roles -- the roles this person keeps a default for (permissions.default_password_roles).
    An empty password field leaves that default as it is. `changes` is what to save.
    """

    MIN_LENGTH = 8

    def __init__(self, *args, roles, **kwargs):
        super().__init__(*args, **kwargs)
        self.roles = list(roles)
        for role in self.roles:
            label = Role(role).label
            self.fields[f"password_{role}"] = forms.CharField(
                required=False, strip=False, label=f"{label} default password",
                widget=forms.PasswordInput(
                    attrs={"class": "input", "autocomplete": "new-password"}, render_value=False,
                ),
            )
            self.fields[f"clear_{role}"] = forms.BooleanField(
                required=False, label="Remove",
                widget=forms.CheckboxInput(attrs={"class": "size-4 accent-accent"}),
            )

    def clean(self):
        cleaned = super().clean()
        for role in self.roles:
            password = cleaned.get(f"password_{role}") or ""
            if password and cleaned.get(f"clear_{role}"):
                self.add_error(f"password_{role}", "Type a password or tick Remove, not both.")
            elif password and len(password) < self.MIN_LENGTH:
                self.add_error(f"password_{role}", f"Use at least {self.MIN_LENGTH} characters.")
        return cleaned

    @property
    def changes(self):
        """{role: ("set", password) | ("clear", None)} for what the person filled in."""
        out = {}
        for role in self.roles:
            password = self.cleaned_data.get(f"password_{role}") or ""
            if password:
                out[role] = ("set", password)
            elif self.cleaned_data.get(f"clear_{role}"):
                out[role] = ("clear", None)
        return out