"""Give every existing account a user_settings row (build plan, rule 9.7).

New accounts get theirs from theming.signals; this covers the ones created before that signal
existed. It only adds missing rows, so running it twice changes nothing, and it never touches
a row that is already there. Rename the file to the next number in apps/theming/migrations/
and set the dependency below to the latest theming migration.
"""
from django.conf import settings
from django.db import migrations


def create_missing_settings(apps, schema_editor):
    """Create a default row (system color mode, bottom bar) for every user that has none."""
    app_label, model_name = settings.AUTH_USER_MODEL.split(".")
    User = apps.get_model(app_label, model_name)
    UserSettings = apps.get_model("theming", "UserSettings")
    have_row = UserSettings.objects.values_list("user_id", flat=True)
    missing = User.objects.exclude(pk__in=have_row).values_list("pk", flat=True)
    # UserSettings is keyed by the user, so bulk_create is safe here (the bulk_create warning
    # in AGENTS.md is about creating Users, whose account id is generated in save()).
    UserSettings.objects.bulk_create([UserSettings(user_id=pk) for pk in missing], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [
        ("theming", "REPLACE_WITH_LATEST_THEMING_MIGRATION"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [migrations.RunPython(create_missing_settings, migrations.RunPython.noop)]
