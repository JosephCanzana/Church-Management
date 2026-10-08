"""Adds submission_token: one row per form submission already processed.

A form that must not run twice (create a person, reset a password, a bulk action)
carries a random one-time token. The service inserts it inside the same
transaction as the action; the primary key makes a second, simultaneous insert
wait and then fail, so the duplicate does nothing. See core.services.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0002_seed_and_triggers"),
    ]

    operations = [
        migrations.CreateModel(
            name="SubmissionToken",
            fields=[
                ("token", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "submission_token",
                "indexes": [models.Index(fields=["created_at"], name="ix_submission_token_created")],
            },
        ),
    ]
