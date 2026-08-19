import uuid

from django.conf import settings
from django.db import models

from apps.catalog.models import Product
from apps.contacts.models import Contact
from apps.organizations.models import Location, OrganizationOwnedModel
from apps.inventory.models import StockUnit


def purchase_attachment_upload_path(instance, filename):
    return f"organizations/{instance.organization_id}/purchases/{instance.id}/{filename}"


class PurchaseStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    APPROVED = "approved", "Approved"
    PART_RECEIVED = "part_received", "Partially received"
    RECEIVED = "received", "Received"
    DISCREPANCY = "discrepancy", "Receipt discrepancy"
    CLOSED = "closed", "Closed"
    CANCELLED = "cancelled", "Cancelled"


class PurchaseDocumentExtractionStatus(models.TextChoices):
    NOT_REQUESTED = "not_requested", "Not requested"
    EXTRACTED = "extracted", "Extracted"
    MANUAL_REVIEW = "manual_review", "Manual review required"
    FAILED = "failed", "Failed"


class PurchaseOrder(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=40)
    supplier = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="purchase_orders")
    destination = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="purchase_orders")
    status = models.CharField(max_length=24, choices=PurchaseStatus.choices, default=PurchaseStatus.DRAFT)
    ordered_on = models.DateField()
    supplier_reference = models.CharField(max_length=80, blank=True)
    attachment = models.FileField(upload_to=purchase_attachment_upload_path, blank=True, max_length=500)
    extraction_status = models.CharField(
        max_length=24,
        choices=PurchaseDocumentExtractionStatus.choices,
        default=PurchaseDocumentExtractionStatus.NOT_REQUESTED,
    )
    extracted_text = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        indexes = [
            models.Index(
                fields=("organization", "status", "ordered_on"),
                name="po_org_st_date",
            ),
        ]
        constraints = [models.UniqueConstraint(fields=("organization", "number"), name="unique_purchase_number_per_org")]

    def __str__(self):
        return self.number


class PurchaseOrderLine(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    received_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2)


class PurchaseReceipt(OrganizationOwnedModel):
    """Durable idempotency record for one receiving command."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField()
    line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name="receipts")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    damaged_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="purchase_receipts",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "request_id"),
                name="unique_purchase_receipt_request_per_org",
            )
        ]


class SupplierReturnStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    COMPLETED = "completed", "Completed"
    REJECTED = "rejected", "Rejected"


class SupplierReturn(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=40)
    line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name="supplier_returns")
    stock_unit = models.ForeignKey(StockUnit, on_delete=models.PROTECT, null=True, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=SupplierReturnStatus.choices, default=SupplierReturnStatus.REQUESTED)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requested_supplier_returns")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="approved_supplier_returns")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("organization", "number"), name="unique_supplier_return_number")
        ]

    @property
    def credit_amount(self):
        return self.quantity * self.line.unit_cost


class PurchaseDiscrepancyStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    RESOLVED = "resolved", "Resolved"


class PurchaseDiscrepancy(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=40)
    line = models.ForeignKey(PurchaseOrderLine, on_delete=models.PROTECT, related_name="discrepancies")
    expected_quantity = models.DecimalField(max_digits=14, decimal_places=3)
    accepted_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    damaged_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    missing_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=PurchaseDiscrepancyStatus.choices, default=PurchaseDiscrepancyStatus.PENDING)
    resolution = models.CharField(max_length=40, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("organization", "number"), name="unique_purchase_discrepancy_number")
        ]

# Create your models here.
