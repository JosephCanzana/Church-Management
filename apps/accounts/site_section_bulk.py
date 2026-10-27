"""Registers the "Bulk add" section on the Site settings page (order 60, after Retention).

Import this module once, the same way the other sections are imported (for example from the accounts url
module), so the registration runs. The shell needs no change.
"""
from apps.accounts.site_sections import SiteSection, register_section  # VERIFY field names against site_sections.py

register_section(SiteSection(
    key="bulk-add",
    title="Bulk add",
    icon="user-plus",  # must exist in static/icons/sprite.svg; check, or swap for an icon that does
    template="accounts/superadmin/_bulk_import.html",
    context=lambda request: {"bulk_kinds": ("extensions", "people")},
    order=60,
))
