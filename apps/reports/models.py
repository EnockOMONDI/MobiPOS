import uuid

from django.conf import settings
from django.db import models

from apps.organizations.models import OrganizationOwnedModel


class ReportExportStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"
    EXPIRED = "expired", "Expired"


def report_export_upload_path(instance, filename):
    return f"organizations/{instance.organization_id}/report-exports/{instance.id}/{filename}"


class ReportExport(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="report_exports",
    )
    module = models.CharField(max_length=80)
    export_format = models.CharField(max_length=8, choices=(("csv", "CSV"), ("pdf", "PDF")))
    filters = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=ReportExportStatus.choices,
        default=ReportExportStatus.PENDING,
    )
    file = models.FileField(upload_to=report_export_upload_path, blank=True, max_length=500)
    row_count = models.PositiveIntegerField(default=0)
    error_reference = models.CharField(max_length=80, blank=True)
    error_message = models.CharField(max_length=255, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=("organization", "requested_by", "status", "created_at"),
                name="report_export_inbox",
            ),
            models.Index(fields=("status", "expires_at"), name="report_export_expiry"),
        ]

    def __str__(self):
        return f"{self.module} {self.export_format.upper()} ({self.get_status_display()})"

# Create your models here.
