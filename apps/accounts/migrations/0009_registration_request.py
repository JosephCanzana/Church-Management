from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("accounts", "0008_person_type_and_password_change"),
    ]
    operations = [
        migrations.CreateModel(
            name="RegistrationRequest",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("first_name", models.CharField(max_length=150)),
                ("middle_name", models.CharField(blank=True, max_length=150)),
                ("last_name", models.CharField(max_length=150)),
                ("suffix", models.CharField(blank=True, max_length=10)),
                ("birth_date", models.DateField()),
                ("gender", models.CharField(choices=[("male", "Male"), ("female", "Female")], max_length=10)),
                ("email", models.EmailField(max_length=254)),
                ("phone_number", models.CharField(max_length=20)),
                ("building_number", models.CharField(blank=True, max_length=50)),
                ("street", models.CharField(blank=True, max_length=150)),
                ("barangay", models.CharField(max_length=150)),
                ("municipality", models.CharField(max_length=150)),
                ("province", models.CharField(max_length=150)),
                ("country", models.CharField(max_length=150)),
                ("postal_code", models.CharField(max_length=10)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("approved", "Approved"), ("rejected", "Rejected"), ("duplicate", "Duplicate")], default="pending", max_length=20)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("decided_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="decided_registration_requests", to=settings.AUTH_USER_MODEL)),
                ("extension", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="registration_requests", to="accounts.extension")),
                ("matched_user", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="registration_requests", to=settings.AUTH_USER_MODEL)),
            ],
            options={"db_table": "registration_request"},
        ),
        migrations.AddIndex(model_name="registrationrequest", index=models.Index(fields=["status", "created_at"], name="ix_reg_request_status")),
        migrations.AddIndex(model_name="registrationrequest", index=models.Index(fields=["first_name", "last_name", "birth_date"], name="ix_reg_request_match")),
    ]
