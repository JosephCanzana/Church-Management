"""LAST migration to add (Stage 7). Creates:
  - account_number_seq (account IDs start at 1000)
  - seed rows: special roles, site settings, retention policy,
    the Zinc theme, admin-alert rules
  - set_purge_at() trigger + partial purge_at indexes on archivable tables

If your first core migration is not called 0001_initial, or an app has
more than one migration, adjust the names in `dependencies` (every app
only needs to be at the migration that creates its tables).
"""
from django.db import migrations

ARCHIVABLE_TABLES = [
    "app_user", "extension", "goal", "prayer_request", "event",
    "monthly_theme", "weekly_powerpoint", "general_resource",
    "attendance", "tithes_offering",
]

SET_PURGE_AT_FN = """
CREATE FUNCTION set_purge_at() RETURNS trigger AS $$
DECLARE
    days int;
BEGIN
    IF NEW.archived_at IS NULL THEN
        NEW.purge_at := NULL;
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF OLD.archived_at IS NOT DISTINCT FROM NEW.archived_at THEN
            RETURN NEW;
        END IF;
    END IF;
    SELECT purge_after_days INTO days FROM retention_policy WHERE entity_type = TG_TABLE_NAME;
    IF days IS NULL THEN
        NEW.purge_at := NULL;
    ELSE
        NEW.purge_at := NEW.archived_at + make_interval(days => days);
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

FORWARD_SQL = ["CREATE SEQUENCE IF NOT EXISTS account_number_seq START WITH 1000 INCREMENT BY 1", SET_PURGE_AT_FN]
REVERSE_SQL = []
for t in ARCHIVABLE_TABLES:
    FORWARD_SQL += [
        f"CREATE TRIGGER trg_set_purge_at BEFORE INSERT OR UPDATE OF archived_at ON {t} "
        f"FOR EACH ROW EXECUTE FUNCTION set_purge_at()",
        f"CREATE INDEX ix_{t}_purge_at ON {t} (purge_at) WHERE purge_at IS NOT NULL",
    ]
    REVERSE_SQL += [f"DROP INDEX IF EXISTS ix_{t}_purge_at", f"DROP TRIGGER IF EXISTS trg_set_purge_at ON {t}"]
REVERSE_SQL += ["DROP FUNCTION IF EXISTS set_purge_at()", "DROP SEQUENCE IF EXISTS account_number_seq"]

SITE_SETTINGS = {
    "default_language": "en",
    "inactive_after_days": 365,
    "auto_transfer_months": 6,
    "event_reminder_default_minutes": 1440,
    "event_reminder_options": [60, 180, 1440, 2880, 10080],
}

RETENTION = [  # None = never auto-delete
    ("app_user", 365), ("extension", 365), ("goal", 180), ("prayer_request", 90),
    ("event", 90), ("monthly_theme", 180), ("weekly_powerpoint", 180),
    ("general_resource", 180), ("attendance", None), ("tithes_offering", None),
    ("notification", 30), ("audit_log", 730),
]

ZINC = {
    "light": dict(
        color_primary="#18181B", color_primary_hover="#27272A", color_primary_soft="#F4F4F5",
        color_bg="#FAFAFA", color_surface="#FFFFFF", color_surface_alt="#F4F4F5",
        color_txt_primary="#18181B", color_txt_secondary="#52525B", color_txt_muted="#A1A1AA",
        color_border="#E4E4E7", color_border_strong="#D4D4D8",
        color_success="#16A34A", color_error="#DC2626", color_warning="#D97706", color_info="#2563EB",
        color_accent="#3B82F6", color_accent_hover="#2563EB", color_accent_soft="#EFF6FF",
    ),
    "dark": dict(
        color_primary="#FAFAFA", color_primary_hover="#E4E4E7", color_primary_soft="#27272A",
        color_bg="#09090B", color_surface="#18181B", color_surface_alt="#27272A",
        color_txt_primary="#FAFAFA", color_txt_secondary="#A1A1AA", color_txt_muted="#71717A",
        color_border="#27272A", color_border_strong="#3F3F46",
        color_success="#22C55E", color_error="#EF4444", color_warning="#F59E0B", color_info="#3B82F6",
        color_accent="#60A5FA", color_accent_hover="#3B82F6", color_accent_soft="#172554",
    ),
}

RULES = [
    ("account.role_change", "A user's role was changed"),
    ("account.delete", "An account was force-deleted"),
    ("account.bulk_archive", "Several accounts were archived at once"),
    ("account.auto_archive", "Accounts were archived automatically for inactivity"),
    ("special_role.grant", "A special role was granted or its limit changed"),
    ("extension.archive", "An extension was archived"),
    ("extension.delete", "An extension was deleted"),
    ("extension.coordinator_change", "An extension's coordinator was changed"),
    ("default_password.change", "A default password was changed"),
    ("attendance.reopen", "An uploaded attendance record was reopened"),
    ("tithes.reopen", "An uploaded tithes & offering record was reopened"),
    ("donation_account.change", "A donation account or its details were changed"),
    ("theme.change", "A theme was edited, disabled or deleted"),
    ("retention_policy.change", "An auto-delete period was changed"),
    ("purge.run", "The nightly job permanently deleted archived records"),
    ("auth.failed_login_burst", "Many failed logins for one account or IP"),
]


def seed(apps, schema_editor):
    SpecialRole = apps.get_model("accounts", "SpecialRole")
    SiteSetting = apps.get_model("core", "SiteSetting")
    RetentionPolicy = apps.get_model("core", "RetentionPolicy")
    Theme = apps.get_model("theming", "Theme")
    ThemePalette = apps.get_model("theming", "ThemePalette")
    NotificationRule = apps.get_model("notifications", "NotificationRule")

    for code, name in [("tithes_offering", "Tithes & offering"), ("attendance", "Attendance")]:
        SpecialRole.objects.update_or_create(code=code, defaults={"name": name})
    for key, value in SITE_SETTINGS.items():
        SiteSetting.objects.get_or_create(key=key, defaults={"value": value})
    for entity, days in RETENTION:
        RetentionPolicy.objects.get_or_create(entity_type=entity, defaults={"purge_after_days": days})
    theme, _ = Theme.objects.get_or_create(
        name="Zinc", defaults={"is_default": True, "is_builtin": True},
    )
    for mode, colors in ZINC.items():
        ThemePalette.objects.get_or_create(theme=theme, mode=mode, defaults=colors)
    for action, description in RULES:
        NotificationRule.objects.get_or_create(
            audit_action=action,
            defaults={"description": description, "notify_roles": ["admin", "super_admin"]},
        )


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_initial"),
        ("accounts", "0001_initial"),
        ("theming", "0001_initial"),
        ("events", "0001_initial"),
        ("attendance", "0001_initial"),
        ("tithes", "0001_initial"),
        ("faith", "0001_initial"),
        ("resources", "0001_initial"),
        ("prayer", "0001_initial"),
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.RunSQL(FORWARD_SQL, REVERSE_SQL),
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
