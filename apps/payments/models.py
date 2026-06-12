import uuid

from django.conf import settings
from django.db import models

from apps.contacts.models import Contact
from apps.organizations.models import OrganizationOwnedModel
from apps.sales.models import Sale


class PaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    MPESA = "mpesa", "M-Pesa"
    CARD = "card", "Card"
    BANK = "bank", "Bank transfer"
    CREDIT = "credit", "Customer credit"


class PaymentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    CONFIRMED = "confirmed", "Confirmed"
    FAILED = "failed", "Failed"
    REFUNDED = "refunded", "Refunded"


class Payment(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=40)
    customer = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="payments", null=True, blank=True)
    sale = models.ForeignKey(Sale, on_delete=models.PROTECT, related_name="payments", null=True, blank=True)
    method = models.CharField(max_length=20, choices=PaymentMethod.choices)
    status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    provider_reference = models.CharField(max_length=120, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)

    class Meta:
        constraints = [models.UniqueConstraint(fields=("organization", "number"), name="unique_payment_number_per_org")]

    def __str__(self):
        return self.number


class Refund(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=40)
    payment = models.ForeignKey(Payment, on_delete=models.PROTECT, related_name="refunds")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=PaymentStatus.choices, default=PaymentStatus.PENDING)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="approved_refunds")

    class Meta:
        constraints = [models.UniqueConstraint(fields=("organization", "number"), name="unique_refund_number_per_org")]

# Create your models here.
