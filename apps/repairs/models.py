import uuid
from decimal import Decimal
from functools import cached_property

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django_ckeditor_5.fields import CKEditor5Field

from apps.contacts.models import Contact
from apps.catalog.models import Product
from apps.inventory.models import StockMovement, StockUnit
from apps.organizations.models import Branch, Location, OrganizationOwnedModel


class RepairStatus(models.TextChoices):
    RECEIVED = "received", "Received"
    DIAGNOSING = "diagnosing", "Diagnosing"
    AWAITING_APPROVAL = "awaiting_approval", "Awaiting approval"
    IN_REPAIR = "in_repair", "In repair"
    QUALITY_CHECK = "quality_check", "Quality check"
    READY = "ready", "Ready for collection"
    CLOSED = "closed", "Closed"


class WarrantyType(models.TextChoices):
    NONE = "none", "Not covered"
    CUSTOMER = "customer", "Customer warranty"
    SUPPLIER = "supplier", "Supplier warranty"
    INTERNAL = "internal", "Internal goodwill"


class RepairPaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    MPESA = "mpesa", "M-Pesa"
    CARD = "card", "Card"
    BANK = "bank", "Bank transfer"


class RepairTicket(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=40)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="repair_tickets")
    customer = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="repair_tickets")
    stock_unit = models.ForeignKey(StockUnit, on_delete=models.PROTECT, related_name="repair_tickets", null=True, blank=True)
    status = models.CharField(max_length=24, choices=RepairStatus.choices, default=RepairStatus.RECEIVED)
    issue = models.TextField()
    diagnosis = CKEditor5Field(config_name="default", blank=True)
    technician = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="repair_tickets")
    warranty = models.BooleanField(default=False)
    warranty_type = models.CharField(max_length=20, choices=WarrantyType.choices, default=WarrantyType.NONE)
    warranty_decision_notes = models.TextField(blank=True)
    quoted_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    collected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("organization", "branch", "status", "created_at"),
                name="repair_org_br_st_ct",
            ),
        ]
        constraints = [models.UniqueConstraint(fields=("organization", "number"), name="unique_repair_number_per_org")]

    def __str__(self):
        return self.number

    @property
    def amount_due(self):
        if self.warranty:
            return Decimal("0.00")
        return self.quoted_amount

    @cached_property
    def amount_paid(self):
        return sum(
            (payment.amount for payment in self.payments.filter(reversed_at__isnull=True)),
            start=Decimal("0.00"),
        )

    @property
    def outstanding_amount(self):
        return max(self.amount_due - self.amount_paid, Decimal("0.00"))


class RepairTransition(OrganizationOwnedModel):
    """Append-only evidence for every repair lifecycle transition."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(RepairTicket, on_delete=models.PROTECT, related_name="transitions")
    from_status = models.CharField(max_length=24, choices=RepairStatus.choices)
    to_status = models.CharField(max_length=24, choices=RepairStatus.choices)
    notes = models.TextField(blank=True)
    transitioned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="repair_transitions",
    )
    transitioned_at = models.DateTimeField()

    class Meta:
        ordering = ("transitioned_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "request_id"),
                name="unique_repair_transition_request_per_org",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Repair transitions are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Repair transitions cannot be deleted.")


class RepairPartUsage(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(RepairTicket, on_delete=models.PROTECT, related_name="parts_used")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    stock_unit = models.ForeignKey(StockUnit, on_delete=models.PROTECT, null=True, blank=True)
    location = models.ForeignKey(Location, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    used_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    movement = models.OneToOneField(
        StockMovement,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="repair_part_usage",
    )
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversed_repair_parts",
    )
    reversal_reason = models.TextField(blank=True)
    reversal_movement = models.OneToOneField(
        StockMovement,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversed_repair_part_usage",
    )

    class Meta:
        ordering = ("created_at",)
        constraints = [
            models.CheckConstraint(condition=models.Q(quantity__gt=0), name="repair_part_quantity_positive"),
            models.UniqueConstraint(
                fields=("organization", "request_id"),
                name="unique_repair_part_request_per_org",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Repair part usage is immutable. Reverse it instead.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Repair part usage cannot be deleted. Reverse it instead.")


class RepairPayment(OrganizationOwnedModel):
    """Append-only payment evidence for a repair ticket."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=40)
    ticket = models.ForeignKey(RepairTicket, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=20, choices=RepairPaymentMethod.choices)
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="repair_payments_received",
    )
    received_at = models.DateTimeField()
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversed_repair_payments",
    )
    reversal_reason = models.TextField(blank=True)

    class Meta:
        ordering = ("-received_at",)
        indexes = [
            models.Index(fields=("organization", "ticket", "received_at"), name="repair_payment_history_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="repair_payment_amount_positive"),
            models.UniqueConstraint(
                fields=("organization", "request_id"),
                name="unique_repair_payment_request_per_org",
            ),
            models.UniqueConstraint(
                fields=("organization", "number"),
                name="unique_repair_payment_number_per_org",
            ),
            models.UniqueConstraint(
                fields=("organization", "reference"),
                condition=~models.Q(reference=""),
                name="unique_repair_payment_reference_per_org",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Repair payments are immutable. Reverse the payment instead.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Repair payments cannot be deleted. Reverse the payment instead.")

    def __str__(self):
        return f"{self.number} - {self.ticket.number}"

# Create your models here.
