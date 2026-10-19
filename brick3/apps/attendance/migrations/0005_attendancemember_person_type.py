from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("attendance", "0004_attendancefirsttimer_guest"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancemember",
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
    ]
