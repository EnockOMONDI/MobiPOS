import uuid

from django.conf import settings
from django.db import models

from apps.catalog.models import Product
from apps.organizations.models import OrganizationOwnedModel
from apps.sales.models import Sale


class CommissionRule(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, null=True, blank=True)
    percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    fixed_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    requires_full_payment = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)


class CommissionAccrual(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="commissions")
    agent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="commission_accruals")
    rule = models.ForeignKey(CommissionRule, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    is_payable = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "sale", "agent", "rule"),
                name="unique_commission_accrual",
            )
        ]


class CommissionPayoutStatus(models.TextChoices):
    REQUESTED = "requested", "Requested"
    APPROVED = "approved", "Approved"
    PAID = "paid", "Paid"
    REJECTED = "rejected", "Rejected"


class CommissionPayout(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=40)
    agent = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="commission_payouts")
    period_start = models.DateField()
    period_end = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=CommissionPayoutStatus.choices, default=CommissionPayoutStatus.REQUESTED)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requested_commission_payouts")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="approved_commission_payouts")
    paid_at = models.DateTimeField(null=True, blank=True)
    payment_reference = models.CharField(max_length=120, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("organization", "number"), name="unique_commission_payout_number")
        ]


class CommissionPayoutLine(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payout = models.ForeignKey(CommissionPayout, on_delete=models.PROTECT, related_name="lines")
    accrual = models.OneToOneField(CommissionAccrual, on_delete=models.PROTECT, related_name="payout_line")
    amount = models.DecimalField(max_digits=14, decimal_places=2)

# Create your models here.
