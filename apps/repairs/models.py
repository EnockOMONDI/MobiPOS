import uuid

from django.conf import settings
from django.db import models
from django_ckeditor_5.fields import CKEditor5Field

from apps.contacts.models import Contact
from apps.catalog.models import Product
from apps.inventory.models import StockUnit
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
        constraints = [models.UniqueConstraint(fields=("organization", "number"), name="unique_repair_number_per_org")]

    def __str__(self):
        return self.number


class RepairPartUsage(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(RepairTicket, on_delete=models.PROTECT, related_name="parts_used")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    stock_unit = models.ForeignKey(StockUnit, on_delete=models.PROTECT, null=True, blank=True)
    location = models.ForeignKey(Location, on_delete=models.PROTECT)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    used_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

# Create your models here.
