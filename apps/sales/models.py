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


class SaleChannel(models.TextChoices):
    CASH = "cash", "Cash"
    CREDIT = "credit", "Credit"


class CreditAgency(models.TextChoices):
    NONE = "", "Not applicable"
    WATU = "watu", "Watu Credit"
    MKOPA = "mkopa", "M-Kopa"
    ONFON = "onfon", "OnFon"
    MOGO = "mogo", "Mogo"
    OTHER = "other", "Other"


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
    sale_channel = models.CharField(max_length=20, choices=SaleChannel.choices, default=SaleChannel.CASH)
    credit_agency = models.CharField(max_length=20, choices=CreditAgency.choices, blank=True)
    customer_national_id = models.CharField(max_length=80, blank=True)
    next_of_kin_name = models.CharField(max_length=200, blank=True)
    next_of_kin_phone = models.CharField(max_length=32, blank=True)
    due_on = models.DateField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="created_sales")

    class Meta:
        indexes = [
            models.Index(
                fields=("organization", "location", "status", "completed_at"),
                name="sale_org_loc_st_done",
            ),
            models.Index(
                fields=("organization", "customer", "completed_at"),
                name="sale_org_cust_done",
            ),
        ]
        constraints = [
            models.UniqueConstraint(fields=("organization", "number"), name="unique_sale_number_per_org"),
            models.UniqueConstraint(
                fields=("organization", "session", "created_by"),
                condition=models.Q(status=SaleStatus.DRAFT),
                name="unique_active_cart_per_session_user",
            ),
        ]

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

    @property
    def total_after_tax(self):
        return self.line_total + self.tax - self.discount


class ReturnStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    APPROVED = "approved", "Approved"
    COMPLETED = "completed", "Completed"
    REJECTED = "rejected", "Rejected"


class ReturnOutcome(models.TextChoices):
    REFUND = "refund", "Refund"
    EXCHANGE = "exchange", "Exchange"
    STORE_CREDIT = "store_credit", "Store credit"
    REPAIR = "repair", "Send for repair"


class ReturnDisposition(models.TextChoices):
    RESTOCK = "restock", "Return to available stock"
    DAMAGED = "damaged", "Damaged / quarantine"
    REPAIR = "repair", "Warranty repair"


class SaleReturn(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=40)
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="returns")
    status = models.CharField(max_length=20, choices=ReturnStatus.choices, default=ReturnStatus.REQUESTED)
    reason = models.TextField()
    refund_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    outcome = models.CharField(max_length=24, choices=ReturnOutcome.choices, default=ReturnOutcome.REFUND)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requested_returns")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="approved_returns")

    class Meta:
        constraints = [models.UniqueConstraint(fields=("organization", "number"), name="unique_return_number_per_org")]


class SaleReturnLine(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sale_return = models.ForeignKey(SaleReturn, on_delete=models.CASCADE, related_name="lines")
    sale_line = models.ForeignKey(SaleLine, on_delete=models.PROTECT, related_name="return_lines")
    quantity = models.DecimalField(max_digits=14, decimal_places=3)
    disposition = models.CharField(
        max_length=24,
        choices=ReturnDisposition.choices,
        default=ReturnDisposition.RESTOCK,
    )
    refundable_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("sale_return", "sale_line"),
                name="unique_sale_line_per_return",
            )
        ]

# Create your models here.
