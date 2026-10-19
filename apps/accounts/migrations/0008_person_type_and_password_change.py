"""Access revamp, brick 1: people types, and the end of "not activated".

  - adds `app_user.person_type` (guest, member_record, member_account; default member_account);
  - trims `status` to active, suspended, archived (`not_activated` goes away);
  - turns any leftover `not_activated` row into `active` with `must_change_password=True`
    (the old "needs activation" is now only the password-change flag);
  - guests and no-account members must have the `member` role (database check).

Nothing here touches the `archived` status, so the purge trigger and
`app_user_archived_matches_status` are unchanged.
"""
from django.db import migrations, models
from django.db.models import Q


def not_activated_to_active(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(status="not_activated").update(status="active", must_change_password=True)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0007_extension_is_main_church_user_age_network_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="person_type",
            field=models.CharField(
                choices=[
                    ("guest", "Guest"),
                    ("member_record", "Member (no account)"),
                    ("member_account", "Member"),
                ],
                default="member_account",
                max_length=20,
            ),
        ),
        # Data first, then the narrower choices, so no row ever holds a value the field forgets.
        migrations.RunPython(not_activated_to_active, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="user",
            name="status",
            field=models.CharField(
                choices=[
                    ("active", "Active"),
                    ("suspended", "Suspended"),
                    ("archived", "Archived"),
                ],
                default="active",
                max_length=20,
            ),
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(
                condition=Q(("person_type", "member_account"), ("role", "member"), _connector="OR"),
                name="app_user_non_account_is_member",
            ),
        ),
    ]
