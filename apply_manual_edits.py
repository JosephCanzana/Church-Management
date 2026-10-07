"""Apply three small edits to files that are not shipped whole in this package.

Run once from the project root (where manage.py is):
    python apply_manual_edits.py

Each edit is an exact-text replacement. It reports APPLIED, ALREADY DONE or NOT FOUND
(file changed since, edit it by hand using the text shown) and never half-writes a file.
"""
from pathlib import Path

EDITS = [
    # 1. Sidebar links point at the new super-admin routes, and only the super-admin sees them.
    ("accounts/navigation.py", 1,
     'NavItem("Extensions", "accounts:extension_list", "building-office", roles=(SUPER_ADMIN, ADMIN), group="Manage"),',
     'NavItem("Extensions", "superadmin:extension_list", "building-office", roles=(SUPER_ADMIN,), group="Manage"),'),
    ("accounts/navigation.py", 1,
     'NavItem("People", "accounts:user_list", "users", roles=LEADERS, group="Manage"),',
     'NavItem("People", "superadmin:user_list", "users", roles=(SUPER_ADMIN,), group="Manage"),'),
    # 2. Mount the super-admin URLs.
    ("church_management/urls.py", 1,
     '    path("accounts/", include("accounts.urls")),\n',
     '    path("accounts/", include("accounts.urls")),\n'
     '    path("superadmin/", include("accounts.urls_superadmin")),\n'),
    # 3. Login tests: the landing page is the role dashboard (/home/ for a member), not "/".
    ("accounts/tests.py", 4,
     'self.assertRedirects(response, "/", fetch_redirect_response=False)',
     'self.assertRedirects(response, reverse("dashboards:member"), fetch_redirect_response=False)'),
]

for path, expected, old, new in EDITS:
    p = Path(path)
    if not p.exists():
        print(f"NOT FOUND   {path} (file missing)")
        continue
    text = p.read_text(encoding="utf-8")
    count = text.count(old)
    if new in text:
        print(f"ALREADY DONE {path}")
    elif count != expected:
        print(f"NOT FOUND   {path}: expected {expected} match(es), found {count}. Edit by hand:\n  replace: {old!r}\n  with:    {new!r}")
    else:
        p.write_text(text.replace(old, new), encoding="utf-8")
        print(f"APPLIED     {path} ({count}x)")
