Brick 9 (personal settings). Unzip in the project root, then:

1. church_management/urls.py: path("settings/", include("apps.theming.urls"))
2. Merge theming_context_processor_snippet.py into apps/theming/context_processors.py
3. Apply public_navbar_change.md to templates/includes/public_navbar.html
4. Remove the light/dark toggle from includes/sidebar_utilities.html (and sidebar_rail.html if present)
5. Create the backfill migration by hand: manage.py makemigrations theming --empty -n backfill_user_settings
   and paste the function and RunPython from backfill_migration_reference.py
6. Run: manage.py check, makemigrations --check, migrate, test apps.theming; make tailwind-build

templates/base.html is REPLACED by this zip (only the <html> tag and the mode script changed).
Delete this folder when done.
