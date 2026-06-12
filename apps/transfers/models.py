import uuid

from django.conf import settings
from django.db import models

from apps.catalog.models import Product
from apps.inventory.models import StockUnit
from apps.organizations.models import Location, OrganizationOwnedModel


class TransferStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    APPROVED = "approved", "Approved"
    IN_TRANSIT = "in_transit", "In transit"
    RECEIVED = "received", "Received"
    DISCREPANCY = "discrepancy", "Received with discrepancy"
    CANCELLED = "cancelled", "Cancelled"


class StockTransfer(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=40)
    source = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="outgoing_transfers")
    destination = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="incoming_transfers")
    status = models.CharField(max_length=20, choices=TransferStatus.choices, default=TransferStatus.REQUESTED)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requested_transfers")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="approved_transfers")
    notes = models.TextField(blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("organization", "number"), name="unique_transfer_number_per_org")]

    def __str__(self):
        return self.number


class StockTransferLine(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transfer = models.ForeignKey(StockTransfer, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    stock_unit = models.ForeignKey(StockUnit, on_delete=models.PROTECT, null=True, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    received_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)


class DiscrepancyStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RESOLVED = "resolved", "Resolved"


class TransferDiscrepancy(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transfer = models.ForeignKey(StockTransfer, on_delete=models.PROTECT, related_name="discrepancies")
    line = models.OneToOneField(StockTransferLine, on_delete=models.PROTECT, related_name="discrepancy")
    expected_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    received_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    difference = models.DecimalField(max_digits=14, decimal_places=3)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=DiscrepancyStatus.choices, default=DiscrepancyStatus.PENDING)
    resolution = models.TextField(blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)

# Create your models here.
