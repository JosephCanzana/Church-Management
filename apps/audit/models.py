"""audit: permanent, append-only trail. No foreign keys on purpose."""
from django.db import models


class AuditLog(models.Model):
    class Source(models.TextChoices):
        WEB = "web", "Web"
        SYSTEM_JOB = "system_job", "System job"

    class Result(models.TextChoices):
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    created_at = models.DateTimeField(auto_now_add=True)
    actor_id = models.BigIntegerField(null=True, blank=True)
    actor_label = models.CharField(max_length=255, null=True, blank=True)
    actor_role = models.CharField(max_length=20, null=True, blank=True)
    extension_id = models.BigIntegerField(null=True, blank=True)
    action = models.CharField(max_length=100)          # 'account.archive', 'attendance.upload'
    entity_type = models.CharField(max_length=100, null=True, blank=True)
    entity_id = models.BigIntegerField(null=True, blank=True)
    entity_label = models.CharField(max_length=255, null=True, blank=True)
    before = models.JSONField(null=True, blank=True)    # changed fields only
    after = models.JSONField(null=True, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.TextField(null=True, blank=True)
    source = models.CharField(max_length=20, choices=Source.choices, default=Source.WEB)
    status = models.CharField(max_length=10, choices=Result.choices, default=Result.SUCCESS)

    class Meta:
        db_table = "audit_log"
        indexes = [
            models.Index(fields=["-created_at"], name="ix_audit_created"),
            models.Index(fields=["actor_id", "-created_at"], name="ix_audit_actor"),
            models.Index(fields=["entity_type", "entity_id"], name="ix_audit_entity"),
            models.Index(fields=["extension_id", "-created_at"], name="ix_audit_ext"),
        ]

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"