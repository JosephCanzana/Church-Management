"""Register the account-registration switch in the shared super-admin settings screen."""
from apps.accounts.site_sections import SiteSection, register_section
from apps.accounts.services.registration import _approval_mail_is_configured, registration_auto_approve


register_section(SiteSection(
    key="registration",
    title="Registration",
    icon="user-plus",
    template="accounts/superadmin/_registration_section.html",
    context=lambda request: {
        "registration_auto_approve": registration_auto_approve(),
        "registration_mail_configured": _approval_mail_is_configured(),
    },
    description="Choose whether matching member records may be claimed without leader review.",
    order=30,
))
