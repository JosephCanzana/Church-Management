"""Adds the 'suspended' status (a reversible login block that is never purged).

Only the field's choices change; no table data is touched and no trigger or
constraint depends on the status values other than 'archived'.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="status",
            field=models.CharField(
                choices=[
                    ("not_activated", "Not activated"),
                    ("active", "Active"),
                    ("suspended", "Suspended"),
                    ("archived", "Archived"),
                ],
                default="not_activated",
                max_length=20,
            ),
        ),
    ]
