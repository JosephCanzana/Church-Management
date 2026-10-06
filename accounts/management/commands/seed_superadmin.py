"""seed_superadmin: create the first super-admin for local development.

Run it after every database reset instead of typing `createsuperuser`:

    docker compose exec web python manage.py seed_superadmin

The password is read from the environment (.env), never written in code:

    SEED_SUPERADMIN_PASSWORD   required
    SEED_SUPERADMIN_FIRST_NAME optional, default "Super"
    SEED_SUPERADMIN_LAST_NAME  optional, default "Admin"

Safe to run twice: if a super-admin already exists it does nothing.
It refuses to run when DEBUG is off, so a seeded password can never end up
on a production database by accident.
"""
from decouple import config
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Role, User


class Command(BaseCommand):
    """Create one super-admin if none exists (development only)."""

    help = "Create the first super-admin from SEED_SUPERADMIN_* environment values (DEBUG only)."

    def handle(self, *args, **options):
        """Check it is safe to seed, then create the account and print its id."""
        if not settings.DEBUG:
            raise CommandError("seed_superadmin only runs with DEBUG=True (development).")

        existing = User.objects.filter(role=Role.SUPER_ADMIN).first()
        if existing is not None:
            self.stdout.write(f"A super-admin already exists: {existing.account_id}. Nothing to do.")
            return

        password = config("SEED_SUPERADMIN_PASSWORD", default="")
        if not password:
            raise CommandError("Set SEED_SUPERADMIN_PASSWORD in your .env file first.")

        with transaction.atomic():
            # create_superuser sets role=super_admin, status=active and
            # must_change_password=False; User.save() generates the account id.
            user = User.objects.create_superuser(
                password=password,
                first_name=config("SEED_SUPERADMIN_FIRST_NAME", default="Super"),
                last_name=config("SEED_SUPERADMIN_LAST_NAME", default="Admin"),
            )

        self.stdout.write(self.style.SUCCESS(f"Super-admin created. Log in with account ID {user.account_id}."))