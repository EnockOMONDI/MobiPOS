import uuid

from django.core.exceptions import ValidationError
from django.db import models

from apps.contacts.models import Contact
from apps.organizations.models import OrganizationOwnedModel
from apps.organizations.models import Branch, Role
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


class InstallmentStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    PART_PAID = "part_paid", "Partially paid"
    PAID = "paid", "Paid"
    OVERDUE = "overdue", "Overdue"


class ReceivableInstallment(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    receivable = models.ForeignKey(Receivable, on_delete=models.CASCADE, related_name="installments")
    sequence = models.PositiveIntegerField()
    schedule_version = models.PositiveIntegerField(default=1)
    is_current = models.BooleanField(default=True)
    replaced_at = models.DateTimeField(null=True, blank=True)
    due_on = models.DateField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    status = models.CharField(max_length=20, choices=InstallmentStatus.choices, default=InstallmentStatus.PENDING)

    class Meta:
        ordering = ("schedule_version", "sequence")
        constraints = [
            models.UniqueConstraint(
                fields=("receivable", "schedule_version", "sequence"),
                name="unique_receivable_installment_version_sequence",
            )
        ]


class Payable(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    supplier = models.ForeignKey(Contact, on_delete=models.PROTECT, related_name="payables")
    purchase_order = models.OneToOneField(PurchaseOrder, on_delete=models.PROTECT, related_name="payable")
    original_amount = models.DecimalField(max_digits=14, decimal_places=2)
    outstanding_amount = models.DecimalField(max_digits=14, decimal_places=2)
    credit_balance = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    due_on = models.DateField()
    is_settled = models.BooleanField(default=False)


class PayablePaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    MPESA = "mpesa", "M-Pesa"
    CARD = "card", "Card"
    BANK = "bank", "Bank transfer"


class PayablePayment(OrganizationOwnedModel):
    """Append-only evidence of money paid to a supplier."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_id = models.UUIDField(default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=40)
    payable = models.ForeignKey(Payable, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=20, choices=PayablePaymentMethod.choices)
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="supplier_payments",
    )
    paid_at = models.DateTimeField()
    reversed_at = models.DateTimeField(null=True, blank=True)
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="reversed_supplier_payments",
    )
    reversal_reason = models.TextField(blank=True)

    class Meta:
        ordering = ("-paid_at",)
        indexes = [
            models.Index(fields=("organization", "payable", "paid_at"), name="payable_payment_history_idx"),
        ]
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="payable_payment_amount_positive"),
            models.UniqueConstraint(
                fields=("organization", "request_id"),
                name="unique_payable_payment_request_per_org",
            ),
            models.UniqueConstraint(
                fields=("organization", "number"),
                name="unique_payable_payment_number_per_org",
            ),
            models.UniqueConstraint(
                fields=("organization", "reference"),
                condition=~models.Q(reference=""),
                name="unique_payable_payment_reference_per_org",
            ),
        ]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Supplier payment records are immutable. Reverse the payment instead.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Supplier payment records cannot be deleted. Reverse the payment instead.")

    def __str__(self):
        return f"{self.number} - {self.payable.supplier}"


class ApprovalStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    APPROVED = "approved", "Approved"
    REJECTED = "rejected", "Rejected"


class ApprovalPolicy(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=120)
    request_type = models.CharField(max_length=80)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, null=True, blank=True, related_name="approval_policies")
    minimum_amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    approver_roles = models.ManyToManyField(Role, blank=True, related_name="approval_policies")
    require_separate_approver = models.BooleanField(default=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("request_type", "minimum_amount")
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "name"),
                name="unique_approval_policy_name_per_org",
            )
        ]

    def __str__(self):
        return self.name


class ApprovalRequest(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_type = models.CharField(max_length=80)
    target_type = models.CharField(max_length=100)
    target_id = models.CharField(max_length=120)
    reason = models.TextField()
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, null=True, blank=True, related_name="approval_requests")
    amount = models.DecimalField(max_digits=14, decimal_places=2, default=0)
    policy = models.ForeignKey(ApprovalPolicy, on_delete=models.PROTECT, null=True, blank=True, related_name="requests")
    policy_snapshot = models.JSONField(default=dict, blank=True)
    previous_request = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="newer_requests")
    status = models.CharField(max_length=20, choices=ApprovalStatus.choices, default=ApprovalStatus.PENDING)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="approval_requests")
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="approval_decisions")
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_notes = models.TextField(blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=("organization", "status", "created_at"),
                name="appr_org_st_created",
            ),
            models.Index(
                fields=("organization", "request_type", "status", "created_at"),
                name="appr_org_type_st_ct",
            ),
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
