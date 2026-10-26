"""Data step: every completed prayer request becomes replied (a reply is now the final state)."""
from django.db import migrations


def completed_to_replied(apps, schema_editor):
    apps.get_model("prayer", "PrayerRequest").objects.filter(status="completed").update(status="replied")


class Migration(migrations.Migration):
    dependencies = [("prayer", "0002_remove_prayerrequest_completed_at_and_more")]

    operations = [migrations.RunPython(completed_to_replied, migrations.RunPython.noop)]