"""accounts.forms: input validation for the accounts app (login so far).

Forms only check the SHAPE of the input (is it empty, does it look like an
account id or an email). Whether the password is actually correct is decided
by the backend in `accounts/backends.py`.
"""
import re

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

# One message for every login failure, so the page never reveals whether the
# account exists, the password was wrong, or the account is archived.
GENERIC_LOGIN_ERROR = "The account ID or password is incorrect."

# Same rule as the `app_user_account_id_format` database constraint.
ACCOUNT_ID_PATTERN = re.compile(r"^[1-9][0-9]{7,}$")


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