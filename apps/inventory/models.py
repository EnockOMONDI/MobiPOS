import uuid

from django import VERSION as DJANGO_VERSION
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from apps.catalog.models import Product
from apps.organizations.models import Location, OrganizationOwnedModel


def check_constraint_kwargs(expression):
    keyword = "condition" if DJANGO_VERSION >= (5, 1) else "check"
    return {keyword: expression}


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
        indexes = [
            models.Index(
                fields=("organization", "status", "location", "product"),
                name="inv_su_org_st_loc_pr",
            ),
            models.Index(
                fields=("organization", "product", "location", "created_at"),
                name="inv_su_org_pr_loc_ct",
            ),
        ]
        constraints = [
            models.UniqueConstraint(fields=("organization", "serial_number"), name="unique_serial_per_org"),
            models.UniqueConstraint(
                fields=("organization", "secondary_serial"),
                condition=~models.Q(secondary_serial=""),
                name="unique_secondary_serial_per_org",
            ),
            models.CheckConstraint(
                **check_constraint_kwargs(
                    models.Q(secondary_serial="") | ~models.Q(serial_number=models.F("secondary_serial"))
                ),
                name="stockunit_primary_secondary_differ",
            ),
        ]

    def clean(self):
        super().clean()
        self.serial_number = (self.serial_number or "").strip()
        self.secondary_serial = (self.secondary_serial or "").strip()
        if self.product_id and not self.product.is_serialized:
            raise ValidationError("Stock units require a serialized product.")
        if self.product_id and self.product.organization_id != self.organization_id:
            raise ValidationError("Product and stock unit must belong to the same organization.")
        if self.location_id and self.location.organization_id != self.organization_id:
            raise ValidationError("Location and stock unit must belong to the same organization.")
        if self.serial_number and self.secondary_serial and self.serial_number == self.secondary_serial:
            raise ValidationError("Primary and secondary serial numbers must be different.")
        if self.organization_id and self.serial_number:
            conflicts = StockUnit.objects.filter(organization_id=self.organization_id).exclude(pk=self.pk)
            if conflicts.filter(
                models.Q(serial_number=self.serial_number) | models.Q(secondary_serial=self.serial_number)
            ).exists():
                raise ValidationError("This serial / IMEI already exists in inventory.")
        if self.organization_id and self.secondary_serial:
            conflicts = StockUnit.objects.filter(organization_id=self.organization_id).exclude(pk=self.pk)
            if conflicts.filter(
                models.Q(serial_number=self.secondary_serial) | models.Q(secondary_serial=self.secondary_serial)
            ).exists():
                raise ValidationError("This secondary serial / IMEI already exists in inventory.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

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
        indexes = [
            models.Index(
                fields=("organization", "stock_unit", "created_at"),
                name="inv_sm_org_su_ct",
            ),
            models.Index(
                fields=("organization", "product", "location", "created_at"),
                name="inv_sm_org_pr_loc_ct",
            ),
        ]

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


class AgedStockActionType(models.TextChoices):
    TRANSFER = "transfer", "Transfer to a faster branch"
    DISCOUNT = "discount", "Run a discount or promotion"
    SUPPLIER_RETURN = "supplier_return", "Return to supplier"
    CAMPAIGN = "campaign", "Add to sales campaign"
    WRITE_OFF = "write_off", "Write off or retire"
    OTHER = "other", "Other action"


class AgedStockActionStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"
    COMPLETED = "completed", "Completed"
    CANCELLED = "cancelled", "Cancelled"


class AgedStockAction(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    stock_unit = models.ForeignKey(StockUnit, on_delete=models.PROTECT, related_name="aged_stock_actions")
    action_type = models.CharField(max_length=40, choices=AgedStockActionType.choices)
    status = models.CharField(max_length=20, choices=AgedStockActionStatus.choices, default=AgedStockActionStatus.REQUESTED)
    reason = models.TextField()
    next_step = models.TextField(blank=True)
    proposal = models.JSONField(default=dict, blank=True)
    execution_result = models.JSONField(default=dict, blank=True)
    approval = models.ForeignKey(
        "operations.ApprovalRequest",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="aged_stock_actions",
    )
    proposed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="proposed_aged_stock_actions",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="approved_aged_stock_actions",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="completed_aged_stock_actions",
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at",)

    def clean(self):
        super().clean()
        if self.stock_unit_id and self.stock_unit.organization_id != self.organization_id:
            raise ValidationError("Aged-stock action and stock unit must belong to the same organization.")
        if self.approval_id and self.approval.organization_id != self.organization_id:
            raise ValidationError("Aged-stock action and approval must belong to the same organization.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_action_type_display()} for {self.stock_unit}"


class StockUnitOfferType(models.TextChoices):
    DISCOUNT = "discount", "Discount"
    CAMPAIGN = "campaign", "Campaign"


class StockUnitOffer(OrganizationOwnedModel):
    """A unit-specific sales offer created from an approved aged-stock action."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    stock_unit = models.ForeignKey(StockUnit, on_delete=models.PROTECT, related_name="offers")
    aged_stock_action = models.OneToOneField(
        AgedStockAction,
        on_delete=models.PROTECT,
        related_name="offer",
    )
    offer_type = models.CharField(max_length=20, choices=StockUnitOfferType.choices)
    name = models.CharField(max_length=160)
    promotional_price = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    starts_on = models.DateField()
    ends_on = models.DateField()
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("-created_at",)

    def clean(self):
        super().clean()
        if self.stock_unit_id and self.stock_unit.organization_id != self.organization_id:
            raise ValidationError("Offer and stock unit must belong to the same organization.")
        if self.aged_stock_action_id and self.aged_stock_action.organization_id != self.organization_id:
            raise ValidationError("Offer and aged-stock action must belong to the same organization.")
        if self.ends_on and self.starts_on and self.ends_on < self.starts_on:
            raise ValidationError("Offer end date cannot be before its start date.")
        if self.offer_type == StockUnitOfferType.DISCOUNT and self.promotional_price is None:
            raise ValidationError("Discount offers require a promotional price.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

# Create your models here.
