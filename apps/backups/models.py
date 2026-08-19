import uuid

from django.conf import settings
from django.db import models


class BackupStatus(models.TextChoices):
    STARTED = "started", "Started"
    COMPLETED = "completed", "Completed"
    FAILED = "failed", "Failed"
    RESTORED = "restored", "Restore verified"
    DELETED = "deleted", "Deleted by retention"


class BackupRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=20, choices=BackupStatus.choices, default=BackupStatus.STARTED, db_index=True)
    storage_backend = models.CharField(max_length=20)
    object_key = models.CharField(max_length=500, blank=True)
    checksum_sha256 = models.CharField(max_length=64, blank=True)
    size_bytes = models.PositiveBigIntegerField(default=0)
    source_database = models.CharField(max_length=100, default="production")
    error = models.TextField(blank=True)
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="started_backup_runs",
    )
    completed_at = models.DateTimeField(null=True, blank=True)
    restored_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
