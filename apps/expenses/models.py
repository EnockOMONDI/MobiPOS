import uuid

from django.conf import settings
from django.db import models

from apps.organizations.models import Branch, OrganizationOwnedModel


class ExpenseStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    SUBMITTED = "submitted", "Submitted"
    APPROVED = "approved", "Approved"
    PAID = "paid", "Paid"
    REJECTED = "rejected", "Rejected"


class ExpensePaymentMethod(models.TextChoices):
    CASH = "cash", "Cash"
    MPESA = "mpesa", "M-Pesa"
    CARD = "card", "Card"
    BANK = "bank", "Bank transfer"


class Expense(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    number = models.CharField(max_length=40)
    branch = models.ForeignKey(Branch, on_delete=models.PROTECT, related_name="expenses")
    category = models.CharField(max_length=100)
    description = models.TextField()
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    status = models.CharField(max_length=20, choices=ExpenseStatus.choices, default=ExpenseStatus.DRAFT)
    incurred_on = models.DateField()
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requested_expenses")
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="approved_expenses")
    approval = models.OneToOneField(
        "operations.ApprovalRequest",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="current_expense",
    )

    class Meta:
        ordering = ("-created_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("organization", "number"),
                name="unique_expense_number_per_org",
            )
        ]

    def __str__(self):
        return f"{self.number} - {self.category}"


class ExpensePayment(OrganizationOwnedModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    expense = models.OneToOneField(Expense, on_delete=models.PROTECT, related_name="payment")
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    method = models.CharField(max_length=20, choices=ExpensePaymentMethod.choices)
    reference = models.CharField(max_length=120, blank=True)
    notes = models.TextField(blank=True)
    paid_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="expense_payments")
    paid_at = models.DateTimeField()

    class Meta:
        ordering = ("-paid_at",)
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name="expense_payment_amount_positive"),
            models.UniqueConstraint(
                fields=("organization", "reference"),
                condition=~models.Q(reference=""),
                name="unique_expense_payment_reference_per_org",
            ),
        ]

    def __str__(self):
        return f"{self.expense.number} - {self.get_method_display()}"

# Create your models here.
