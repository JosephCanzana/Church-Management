"""Schema step: drop the "completed" choice and the completed_at column."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("prayer", "0002_merge_completed_into_replied")]

    operations = [
        migrations.AlterField(
            model_name="prayerrequest",
            name="status",
            field=models.CharField(
                choices=[("open", "Open"), ("replied", "Replied"), ("archived", "Archived")],
                default="open", max_length=10,
            ),
        ),
        migrations.RemoveField(model_name="prayerrequest", name="completed_at"),
    ]
