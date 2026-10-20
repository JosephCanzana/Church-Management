"""Seed the admin-alert rules for archiving an uploaded attendance sheet and force-deleting a sheet.

Save as `apps/core/migrations/0004_seed_attendance_archive_rules.py`, next to `0002_seed_and_triggers.py` (which
seeds the other rules; leave that file exactly as it was) and `0003_submission_token.py`. It sits AFTER 0003 in
core's chain and also needs the notifications tables, so there is still exactly one leaf node.

Same shape as the RULES in `0002_seed_and_triggers`: one `notification_rule` row per audit action, notifying admins
and super-admins. `attendance.archive` (a draft) has NO rule on purpose: a coordinator tidying a draft is not worth
an alert. Safe to run twice (`get_or_create`), and it never overwrites a rule an admin edited.
"""
from django.db import migrations

RULES = [
    ("attendance.archive_uploaded", "An uploaded attendance record was archived"),
    ("attendance.force_delete", "An attendance record was permanently deleted"),
]


def seed(apps, schema_editor):
    NotificationRule = apps.get_model("notifications", "NotificationRule")
    for action, description in RULES:
        NotificationRule.objects.get_or_create(
            audit_action=action,
            defaults={"description": description, "notify_roles": ["admin", "super_admin"]},
        )


def unseed(apps, schema_editor):
    NotificationRule = apps.get_model("notifications", "NotificationRule")
    NotificationRule.objects.filter(audit_action__in=[action for action, _ in RULES]).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_submission_token"),
        ("notifications", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]