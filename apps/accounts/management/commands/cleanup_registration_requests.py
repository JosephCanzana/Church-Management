from django.core.management.base import BaseCommand

from apps.accounts.services.registration import cleanup_decided_requests


class Command(BaseCommand):
    help = "Delete decided registration requests after their retention period."

    def handle(self, *args, **options):
        deleted = cleanup_decided_requests()
        self.stdout.write(f"Deleted {deleted} decided registration request(s).")
