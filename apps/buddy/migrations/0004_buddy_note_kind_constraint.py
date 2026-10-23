"""Brick 6, step 3 of 3: now that every row is consistent, enforce kind <-> body."""
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [("buddy", "0003_backfill_buddy_note_kind")]

    operations = [
        migrations.AddConstraint(
            model_name="buddynote",
            constraint=models.CheckConstraint(
                condition=(
                    Q(kind="message", body__isnull=False)
                    | Q(kind__in=["nudge", "prayed"], body__isnull=True)
                ),
                name="buddy_note_kind_body",
            ),
        ),
    ]
