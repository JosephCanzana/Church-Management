"""Brick 6, step 1 of 3: new columns on user_buddy and buddy_note (no data change yet).

Split into three migrations on purpose: the data backfill (0003) must run BEFORE the
`buddy_note_kind_body` constraint (0004), and keeping schema and data apart avoids Postgres'
"pending trigger events" error.

ASSUMES the last applied buddy migration is `0001_initial`. If yours has another name, change
the one `dependencies` entry below.
"""
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("buddy", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="userbuddy", name="end_requested_by",
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name="+", to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="userbuddy", name="end_requested_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userbuddy", name="end_request_note",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="userbuddy", name="partner_final_note",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddConstraint(
            model_name="userbuddy",
            constraint=models.CheckConstraint(
                condition=Q(end_requested_by__isnull=True) | Q(end_requested_at__isnull=False),
                name="user_buddy_end_request_time",
            ),
        ),
        migrations.AddConstraint(
            model_name="userbuddy",
            constraint=models.CheckConstraint(
                condition=Q(ended_at__isnull=False) | Q(partner_final_note__isnull=True),
                name="user_buddy_partner_note_needs_end",
            ),
        ),
        migrations.AddField(
            model_name="buddynote", name="kind",
            field=models.CharField(
                choices=[("message", "Message"), ("nudge", "Nudge"), ("prayed", "I prayed for you")],
                default="message", max_length=10,
            ),
        ),
        migrations.AddIndex(
            model_name="buddynote",
            index=models.Index(fields=["buddy", "id"], name="ix_buddy_note_buddy_id"),
        ),
    ]
