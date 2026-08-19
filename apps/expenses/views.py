from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.services import record_audit_event
from apps.notifications.models import Notification
from apps.organizations.permissions import (
    organization_owner_required,
    organization_permission_required,
    user_can_access_branch,
    user_has_organization_permission,
)

from .forms import ExpenseForm, ExpensePaymentForm
from .models import Expense, ExpenseStatus
from .services import pay_expense, resubmit_expense, submit_expense_for_approval


def _scoped_expense(request, expense_id):
    expense = get_object_or_404(
        Expense.objects.select_related(
            "branch", "requested_by", "approved_by", "approval", "approval__decided_by", "payment", "payment__paid_by"
        ),
        id=expense_id,
        organization=request.organization,
    )
    if not user_can_access_branch(request.user, request.organization, expense.branch):
        raise PermissionDenied("You do not have access to this expense branch.")
    return expense


@login_required
@organization_permission_required("expenses.add_expense")
@transaction.atomic
def expense_create(request):
    form = ExpenseForm(request.POST or None, organization=request.organization, user=request.user)
    if request.method == "POST" and form.is_valid():
        expense = Expense.objects.create(
            organization=request.organization,
            number=f"EXP-{timezone.now():%Y%m%d%H%M%S%f}",
            requested_by=request.user,
            status=ExpenseStatus.DRAFT,
            **form.cleaned_data,
        )
        expense, approval = submit_expense_for_approval(expense=expense)
        record_audit_event(
            action="expense.submitted",
            actor=request.user,
            organization=request.organization,
            target=expense,
            metadata={"approval_id": str(approval.id), "amount": str(expense.amount)},
            request=request,
        )
        messages.success(request, "Expense submitted. An eligible approver has been notified.")
        return redirect("expense-detail", expense_id=expense.id)
    return render(request, "expenses/create.html", {"form": form})


@login_required
@organization_permission_required("expenses.view_expense")
def expense_detail(request, expense_id):
    expense = _scoped_expense(request, expense_id)
    can_resubmit = expense.status == ExpenseStatus.REJECTED and expense.requested_by_id == request.user.id
    can_pay = (
        expense.status == ExpenseStatus.APPROVED
        and user_has_organization_permission(request.user, request.organization, "expenses.change_expense")
    )
    return render(
        request,
        "expenses/detail.html",
        {
            "expense": expense,
            "payment_form": ExpensePaymentForm(),
            "can_resubmit": can_resubmit,
            "can_pay": can_pay,
        },
    )


@login_required
@organization_permission_required("expenses.add_expense")
@require_POST
def expense_resubmit(request, expense_id):
    expense = _scoped_expense(request, expense_id)
    if expense.requested_by_id != request.user.id:
        raise PermissionDenied("Only the original requester can resubmit this expense.")
    try:
        expense, approval = resubmit_expense(expense=expense)
    except ValidationError as error:
        messages.error(request, error.message)
    else:
        record_audit_event(
            action="expense.resubmitted",
            actor=request.user,
            organization=request.organization,
            target=expense,
            metadata={"approval_id": str(approval.id)},
            request=request,
        )
        messages.success(request, "Expense resubmitted for a new decision.")
    return redirect("expense-detail", expense_id=expense.id)


@login_required
@organization_permission_required("expenses.change_expense")
@require_POST
def expense_pay(request, expense_id):
    expense = _scoped_expense(request, expense_id)
    form = ExpensePaymentForm(request.POST)
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect("expense-detail", expense_id=expense.id)
    try:
        payment = pay_expense(expense=expense, paid_by=request.user, **form.cleaned_data)
    except ValidationError as error:
        messages.error(request, error.message)
    else:
        record_audit_event(
            action="expense.paid",
            actor=request.user,
            organization=request.organization,
            target=expense,
            metadata={
                "payment_id": str(payment.id),
                "method": payment.method,
                "reference": payment.reference,
                "amount": str(payment.amount),
            },
            request=request,
        )
        if expense.requested_by_id != request.user.id:
            Notification.objects.create(
                organization=request.organization,
                recipient=expense.requested_by,
                title="Expense paid",
                message=f"{expense.number} for KES {expense.amount} was marked as paid.",
                link=reverse("expense-detail", args=[expense.id]),
            )
        messages.success(request, "Expense payment recorded with an audit trail.")
    return redirect("expense-detail", expense_id=expense.id)


@login_required
@organization_owner_required
@require_POST
def expense_approve(request, expense_id):
    """Compatibility route that prevents bypassing the approval inbox."""
    expense = _scoped_expense(request, expense_id)
    if expense.approval_id:
        messages.info(request, "Review and decide this expense through the approval request.")
        return redirect("approval-detail", approval_id=expense.approval_id)
    messages.error(request, "This expense has no current approval request.")
    return redirect("expense-detail", expense_id=expense.id)
