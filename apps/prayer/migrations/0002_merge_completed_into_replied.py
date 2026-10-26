"""Data step: every completed prayer request becomes replied (a reply is now the final state)."""
from django.db import migrations


def completed_to_replied(apps, schema_editor):
    PrayerRequest = apps.get_model("prayer", "PrayerRequest")
    PrayerRequest.objects.filter(status="completed").update(status="replied")


class Migration(migrations.Migration):
    dependencies = [("prayer", "0001_initial")]  # (verify) change to your latest prayer migration

    operations = [migrations.RunPython(completed_to_replied, migrations.RunPython.noop)]
