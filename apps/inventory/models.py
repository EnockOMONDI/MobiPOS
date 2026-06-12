import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.catalog.models import Product
from apps.organizations.models import Location, OrganizationOwnedModel


class SerialStatus(models.TextChoices):
    EXPECTED = "expected", "Expected"
    AVAILABLE = "available", "Available"
    RESERVED = "reserved", "Reserved"
    IN_TRANSFER = "in_transfer", "In transfer"
    SOLD = "sold", "Sold"
    RETURNED = "returned", "Returned"
    DAMAGED = "damaged", "Damaged"
    WARRANTY_REPAIR = "warranty_repair", "Warranty repair"
    WRITTEN_OFF = "written_off", "Written off"


class StockUnit(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="stock_units")
    serial_number = models.CharField(max_length=120)
    secondary_serial = models.CharField(max_length=120, blank=True)
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="stock_units", null=True, blank=True)
    status = models.CharField(max_length=24, choices=SerialStatus.choices, default=SerialStatus.EXPECTED)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    warranty_expires_on = models.DateField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("organization", "serial_number"), name="unique_serial_per_org")]

    def clean(self):
        super().clean()
        if self.product_id and not self.product.is_serialized:
            raise ValidationError("Stock units require a serialized product.")
        if self.product_id and self.product.organization_id != self.organization_id:
            raise ValidationError("Product and stock unit must belong to the same organization.")
        if self.location_id and self.location.organization_id != self.organization_id:
            raise ValidationError("Location and stock unit must belong to the same organization.")

    def __str__(self):
        return self.serial_number


class StockMovementType(models.TextChoices):
    OPENING = "opening", "Opening balance"
    PURCHASE_RECEIPT = "purchase_receipt", "Purchase receipt"
    SUPPLIER_RETURN = "supplier_return", "Supplier return"
    TRANSFER_DISPATCH = "transfer_dispatch", "Transfer dispatch"
    TRANSFER_RECEIPT = "transfer_receipt", "Transfer receipt"
    SALE = "sale", "Sale"
    CUSTOMER_RETURN = "customer_return", "Customer return"
    ADJUSTMENT = "adjustment", "Adjustment"
    REPAIR_ISSUE = "repair_issue", "Repair issue"
    REVERSAL = "reversal", "Reversal"


class StockMovement(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    movement_type = models.CharField(max_length=30, choices=StockMovementType.choices)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="stock_movements")
    stock_unit = models.ForeignKey(StockUnit, on_delete=models.PROTECT, related_name="movements", null=True, blank=True)
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="stock_movements")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    reference_type = models.CharField(max_length=80, blank=True)
    reference_id = models.CharField(max_length=120, blank=True)
    reason = models.CharField(max_length=255, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    reversed_movement = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="reversals")

    class Meta:
        ordering = ("-created_at",)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Posted stock movements are immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Posted stock movements cannot be deleted.")


class StockBalance(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="balances")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="balances")
    quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("organization", "product", "location"), name="unique_stock_balance")]


class StockAdjustmentStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    COMPLETED = "completed", "Completed"
    REJECTED = "rejected", "Rejected"


class StockAdjustment(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=40)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="stock_adjustments")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="stock_adjustments")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=StockAdjustmentStatus.choices, default=StockAdjustmentStatus.REQUESTED)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requested_stock_adjustments")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="approved_stock_adjustments")
    movement = models.OneToOneField(StockMovement, on_delete=models.PROTECT, null=True, blank=True, related_name="adjustment")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("organization", "number"), name="unique_stock_adjustment_number")
        ]

# Create your models here.
