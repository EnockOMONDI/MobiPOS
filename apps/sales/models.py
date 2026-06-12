import uuid

from django.conf import settings
from django.db import models

from apps.catalog.models import Product
from apps.contacts.models import Contact
from apps.inventory.models import StockUnit
from apps.organizations.models import Location, OrganizationOwnedModel
from apps.pos.models import POSSession


class SaleStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    COMPLETED = "completed", "Completed"
    PART_PAID = "part_paid", "Partially paid"
    PAID = "paid", "Paid"
    RETURNED = "returned", "Returned"
    REVERSED = "reversed", "Reversed"


class Sale(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=40)
    session = models.ForeignKey(POSSession, on_delete=models.PROTECT, related_name="sales")
    location = models.ForeignKey(Location, on_delete=models.PROTECT, related_name="sales")
    customer = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="sales", null=True, blank=True)
    agent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="agent_sales", null=True, blank=True)
    status = models.CharField(max_length=20, choices=SaleStatus.choices, default=SaleStatus.DRAFT)
    subtotal = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    discount_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    paid_total = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    due_on = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_sales")

    class Meta:
        constraints = [models.UniqueConstraint(fields=("organization", "number"), name="unique_sale_number_per_org")]

    @property
    def balance_due(self):
        return self.total - self.paid_total

    def __str__(self):
        return self.number


class SaleLine(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sale = models.ForeignKey(Sale, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT)
    stock_unit = models.ForeignKey(StockUnit, on_delete=models.PROTECT, null=True, blank=True)
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    unit_price = models.DecimalField(max_digits=14, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    discount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    tax = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    line_total = models.DecimalField(max_digits=14, decimal_places=2)
    returned_quantity = models.DecimalField(max_digits=14, decimal_places=3, default=0)


class ReturnStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    APPROVED = "approved", "Approved"
    COMPLETED = "completed", "Completed"
    REJECTED = "rejected", "Rejected"


class SaleReturn(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=40)
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="returns")
    status = models.CharField(max_length=20, choices=ReturnStatus.choices, default=ReturnStatus.REQUESTED)
    reason = models.TextField()
    refund_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requested_returns")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="approved_returns")

    class Meta:
        constraints = [models.UniqueConstraint(fields=("organization", "number"), name="unique_return_number_per_org")]

# Create your models here.
