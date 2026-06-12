import uuid

from django.db import models

from apps.contacts.models import Contact
from apps.organizations.models import OrganizationOwnedModel
from apps.sales.models import Sale
from apps.purchasing.models import PurchaseOrder
from django.conf import settings


class Receivable(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="receivables")
    sale = models.OneToOneField(Sale, on_delete=models.PROTECT, related_name="receivable")
    original_amount = models.DecimalField(max_digits=14, decimal_places=2)
    outstanding_amount = models.DecimalField(max_digits=14, decimal_places=2)
    due_on = models.DateField()
    is_written_off = models.BooleanField(default=False)


class Payable(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    supplier = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="payables")
    purchase_order = models.OneToOneField(PurchaseOrder, on_delete=models.PROTECT, related_name="payable")
    original_amount = models.DecimalField(max_digits=14, decimal_places=2)
    outstanding_amount = models.DecimalField(max_digits=14, decimal_places=2)
    due_on = models.DateField()
    is_settled = models.BooleanField(default=False)


class ApprovalStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class ApprovalRequest(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_type = models.CharField(max_length=80)
    target_type = models.CharField(max_length=100)
    target_id = models.CharField(max_length=120)
    reason = models.TextField()
    status = models.CharField(max_length=20, choices=ApprovalStatus.choices, default=ApprovalStatus.PENDING)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="approval_requests")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="approval_decisions")
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "request_type", "target_type", "target_id"),
                name="unique_approval_request_target",
            )
        ]

    @property
    def display_type(self):
        return self.request_type.replace("_", " ").title()

    @property
    def requested_permission_code(self):
        if self.request_type == "access_request" and ":" in self.target_id:
            return self.target_id.split(":", maxsplit=1)[1]
        return ""

# Create your models here.
