"""Changing a notification's read state. Both functions are idempotent: a repeat changes nothing."""
from django.db import transaction
from django.utils import timezone

from apps.notifications.models import Notification


def mark_read(user, pk):
    """Mark one of the person's own notifications as read; return how many rows changed (0 or 1)."""
    with transaction.atomic():
        return Notification.objects.filter(user=user, pk=pk, read_at__isnull=True).update(read_at=timezone.now())


def mark_all_read(user):
    """Mark every unread notification of the person as read; return how many changed."""
    with transaction.atomic():
        return Notification.objects.filter(user=user, read_at__isnull=True).update(read_at=timezone.now())
