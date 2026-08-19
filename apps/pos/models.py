import uuid

from django.conf import settings
from django.db import models

from apps.organizations.models import Location, OrganizationOwnedModel


class SessionStatus(models.TextChoices):
    OPEN = "open", "Open"
    CLOSED = "closed", "Closed"
    REVIEWED = "reviewed", "Reviewed"


class POSSession(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=40)
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="pos_sessions")
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="pos_sessions")
    status = models.CharField(max_length=20, choices=SessionStatus.choices, default=SessionStatus.OPEN)
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    opening_float = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    expected_cash = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    actual_cash = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    variance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    closing_note = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("organization", "number"), name="unique_pos_session_number_per_org"),
            models.UniqueConstraint(
                fields=("organization", "cashier", "location"),
                condition=models.Q(status=SessionStatus.OPEN),
                name="unique_open_pos_session_per_cashier_location",
            ),
        ]

    def __str__(self):
        return self.number


class CashMovementType(models.TextChoices):
    CASH_IN = "cash_in", "Cash in"
    CASH_OUT = "cash_out", "Cash out"
    DROP = "drop", "Safe drop"


class CashMovement(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(POSSession, on_delete=models.PROTECT, related_name="cash_movements")
    movement_type = models.CharField(max_length=20, choices=CashMovementType.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.CharField(max_length=255)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="cash_movements")

    class Meta:
        ordering = ("-created_at",)


class OfflineInvoiceQueueStatus(models.TextChoices):
    QUEUED = "queued", "Review required"
    PROCESSING = "processing", "Under review"
    COMPLETED = "completed", "Reconciled"
    FAILED = "failed", "Conflict"
    DISCARDED = "discarded", "Discarded"


class OfflineInvoiceQueue(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client_reference = models.CharField(max_length=120)
    session = models.ForeignKey(POSSession, on_delete=models.PROTECT, related_name="offline_invoices")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="offline_invoices")
    cashier = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="offline_invoices")
    draft_sale = models.ForeignKey(
        "sales.Sale",
        on_delete=models.PROTECT,
        related_name="recovery_drafts",
        null=True,
        blank=True,
    )
    schema_version = models.PositiveSmallIntegerField(default=1)
    device_id = models.CharField(max_length=120, blank=True)
    payload_digest = models.CharField(max_length=64, blank=True)
    payload = models.JSONField(default=dict)
    status = models.CharField(
        max_length=20,
        choices=OfflineInvoiceQueueStatus.choices,
        default=OfflineInvoiceQueueStatus.QUEUED,
    )
    error_message = models.TextField(blank=True)
    sale_number = models.CharField(max_length=40, blank=True)
    synced_at = models.DateTimeField(null=True, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="reviewed_offline_invoices",
        null=True,
        blank=True,
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=("organization", "status", "location", "created_at"), name="pos_recovery_review_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "client_reference"),
                name="unique_offline_invoice_client_reference_per_org",
            )
        ]
