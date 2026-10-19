from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.accounts.models import ArchiveReason, Role, Status, User
from apps.audit.models import AuditLog
from apps.audit.services import log_action
from apps.core import services as core_services


class Command(BaseCommand):
    help = "Archive member accounts and records, including guests, inactive for one year."

    def handle(self, *args, **options):
        cutoff = timezone.localdate() - timedelta(days=365)
        qs = User.objects.filter(role=Role.MEMBER, status=Status.ACTIVE).filter(
            Q(last_attended_at__lt=cutoff) | Q(last_attended_at__isnull=True, created_at__date__lt=cutoff)
        ).order_by("pk")
        count = 0
        for person_id in qs.values_list("pk", flat=True).iterator():
            with transaction.atomic():
                person = User.objects.select_for_update().filter(
                    pk=person_id, role=Role.MEMBER, status=Status.ACTIVE,
                ).first()
                if not person:
                    continue
                old_type = person.person_type
                person.status = Status.ARCHIVED
                person.archive_reason = ArchiveReason.INACTIVE
                core_services.archive(person, extra_fields=["status", "archive_reason"])
                log_action("account.archive_inactive", entity_type="app_user", entity_id=person.pk,
                           entity_label=person.account_id, after={"reason": "inactive", "person_type": old_type},
                           source=AuditLog.Source.SYSTEM_JOB)
                count += 1
        self.stdout.write(f"Archived {count} inactive person(s).")
