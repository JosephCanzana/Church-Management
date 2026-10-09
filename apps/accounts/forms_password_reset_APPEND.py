
# ============================================================ forgot / reset password
# APPEND THIS BLOCK to the end of apps/accounts/forms.py (the imports it needs
# are already at the top of that file).
class ForgotPasswordForm(forms.Form):
    """One box: an account id or a verified email.

    No shape check on purpose: the page must answer the same way for anything
    typed, so a badly shaped value is simply treated as "no match" by the service.
    """

    identifier = forms.CharField(
        max_length=254, strip=True, label="Account ID or email",
        widget=forms.TextInput(attrs={
            "class": "input px-4 py-3.5 text-base font-mono",
            "autocomplete": "username", "autocapitalize": "none", "autofocus": True,
        }),
    )


class ResetPasswordForm(forms.Form):
    """Choose a new password (twice) from the emailed link.

    user -- the person the link belongs to (NOT request.user: nobody is signed in).
    Used to refuse the current password and for Django's similarity validator.
    """

    new_password = forms.CharField(strip=False, widget=forms.PasswordInput, label="New password")
    confirm_password = forms.CharField(strip=False, widget=forms.PasswordInput, label="Confirm new password")

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

    def clean_new_password(self):
        value = self.cleaned_data["new_password"]
        if self.user.check_password(value):
            raise ValidationError("Choose a password different from your current one.")
        validate_strong_password(value)
        validate_password(value, self.user)  # raises ValidationError listing every problem
        return value

    def clean(self):
        cleaned = super().clean()
        new, confirm = cleaned.get("new_password"), cleaned.get("confirm_password")
        if new and confirm and new != confirm:
            self.add_error("confirm_password", "The two passwords do not match.")
        return cleaned
