"""Permanently remove expired archived records and their uploaded files."""
from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import router
from django.db.models.deletion import Collector, ProtectedError
from django.utils import timezone

from apps.core.uploads import delete_files


class Command(BaseCommand):
    """Purge expired archived rows, removing stored files before rows."""
    help = "Permanently delete archived records whose retention period expired."

    def handle(self, *args, **options):
        """Purge eligible records and report the number removed and skipped."""
        removed = 0
        skipped = 0
        now = timezone.now()

        for model in apps.get_models():
            if not _is_archivable(model):
                continue
            record_ids = model._base_manager.filter(
                archived_at__isnull=False,
                purge_at__lte=now,
            ).values_list("pk", flat=True)
            for record_id in list(record_ids):
                try:
                    record = model._base_manager.get(pk=record_id)
                except model.DoesNotExist:
                    continue

                collector = Collector(using=router.db_for_write(model, instance=record))
                try:
                    collector.collect([record])
                except ProtectedError:
                    skipped += 1
                    continue
                if _has_external_restricted_rows(collector):
                    skipped += 1
                    continue

                for objects in collector.data.values():
                    for instance in objects:
                        delete_files(instance)
                for queryset in collector.fast_deletes:
                    for instance in queryset.iterator():
                        delete_files(instance)
                collector.delete()
                removed += 1

        self.stdout.write(f"Purged {removed} archived record(s); skipped {skipped} protected record(s).")


def _is_archivable(model):
    """Return whether a concrete model has both archive and purge fields."""
    field_names = {field.name for field in model._meta.concrete_fields}
    return {"archived_at", "purge_at"}.issubset(field_names)


def _has_external_restricted_rows(collector):
    """Check RESTRICT relations before removing any files from the cascade."""
    for related_model, fields in collector.restricted_objects.items():
        collected = collector.data.get(related_model, set())
        for objects in fields.values():
            if objects.difference(collected):
                return True
    return False