import uuid

from django.conf import settings
from django.db import models

from apps.organizations.models import OrganizationOwnedModel


class Notification(OrganizationOwnedModel):
    class Kind(models.TextChoices):
        GENERAL = "general", "General update"
        APPROVAL_REQUEST = "approval_request", "Approval required"
        APPROVAL_DECISION = "approval_decision", "Approval decision"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="notifications")
    kind = models.CharField(max_length=32, choices=Kind.choices, default=Kind.GENERAL, db_index=True)
    approval = models.ForeignKey(
        "operations.ApprovalRequest",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="notifications",
    )
    title = models.CharField(max_length=200)
    message = models.TextField()
    link = models.CharField(max_length=255, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("organization", "recipient", "read_at", "created_at"), name="notify_inbox_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("recipient", "approval", "kind"),
                condition=models.Q(approval__isnull=False),
                name="unique_approval_notification_recipient_kind",
            )
        ]


class EmailDeliveryStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PROCESSING = "processing", "Processing"
    SENT = "sent", "Sent"
    FAILED = "failed", "Failed"
    DEAD = "dead", "Dead"


class EmailOutbox(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    organization = models.ForeignKey(
        "organizations.Organization",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="email_deliveries",
    )
    recipient = models.EmailField(db_index=True)
    subject = models.CharField(max_length=255)
    text_body = models.TextField(blank=True)
    html_body = models.TextField(blank=True)
    from_email = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=EmailDeliveryStatus.choices, default=EmailDeliveryStatus.PENDING, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True, db_index=True)
    provider_message_id = models.CharField(max_length=255, blank=True)
    last_error = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    content_redacted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [models.Index(fields=("status", "next_attempt_at"), name="email_retry_due_idx")]

# Create your models here.
