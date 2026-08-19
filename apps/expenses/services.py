from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.operations.models import ApprovalStatus
from apps.operations.services import request_approval

from .models import Expense, ExpensePayment, ExpenseStatus


@transaction.atomic
def submit_expense_for_approval(*, expense):
    expense = Expense.objects.select_for_update().get(pk=expense.pk, organization=expense.organization)
    if expense.status not in {ExpenseStatus.DRAFT, ExpenseStatus.REJECTED}:
        raise ValidationError("Only draft or rejected expenses can be submitted.")
    approval = request_approval(
        organization=expense.organization,
        request_type="expense",
        target=expense,
        requested_by=expense.requested_by,
        reason=f"{expense.category}: {expense.description}",
        amount=expense.amount,
        branch=expense.branch,
    )
    expense.status = ExpenseStatus.SUBMITTED
    expense.approval = approval
    expense.approved_by = None
    expense.save(update_fields=["status", "approval", "approved_by", "updated_at"])
    return expense, approval


@transaction.atomic
def sync_expense_from_approval(*, approval):
    if approval.request_type != "expense":
        return None
    expense = Expense.objects.select_for_update().filter(
        organization=approval.organization,
        id=approval.target_id,
        approval=approval,
    ).first()
    if not expense:
        raise ValidationError("The expense linked to this approval is no longer current.")
    if approval.status == ApprovalStatus.APPROVED:
        expense.status = ExpenseStatus.APPROVED
        expense.approved_by = approval.decided_by
    elif approval.status == ApprovalStatus.REJECTED:
        expense.status = ExpenseStatus.REJECTED
        expense.approved_by = None
    else:
        return expense
    expense.save(update_fields=["status", "approved_by", "updated_at"])
    return expense


@transaction.atomic
def resubmit_expense(*, expense):
    locked = Expense.objects.select_for_update().get(pk=expense.pk, organization=expense.organization)
    if locked.status != ExpenseStatus.REJECTED:
        raise ValidationError("Only rejected expenses can be resubmitted.")
    return submit_expense_for_approval(expense=locked)


@transaction.atomic
def pay_expense(*, expense, paid_by, method, reference="", notes=""):
    locked = Expense.objects.select_for_update().get(pk=expense.pk, organization=expense.organization)
    if locked.status != ExpenseStatus.APPROVED:
        raise ValidationError("Only an approved expense can be marked as paid.")
    if ExpensePayment.objects.filter(expense=locked).exists():
        raise ValidationError("This expense already has a payment record.")
    try:
        payment = ExpensePayment.objects.create(
            organization=locked.organization,
            expense=locked,
            amount=locked.amount,
            method=method,
            reference=reference,
            notes=notes,
            paid_by=paid_by,
            paid_at=timezone.now(),
        )
    except IntegrityError as error:
        raise ValidationError("This payment reference has already been used.") from error
    locked.status = ExpenseStatus.PAID
    locked.save(update_fields=["status", "updated_at"])
    return payment
