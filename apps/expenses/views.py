from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.audit.services import record_audit_event
from apps.organizations.permissions import organization_owner_required, organization_permission_required
from .forms import ExpenseForm
from .models import Expense, ExpenseStatus


@login_required
@organization_permission_required("expenses.add_expense")
def expense_create(request):
    form = ExpenseForm(request.POST or None, organization=request.organization, user=request.user)
    if request.method == "POST" and form.is_valid():
        expense = Expense.objects.create(
            organization=request.organization,
            number=f"EXP-{timezone.now():%Y%m%d%H%M%S%f}",
            requested_by=request.user,
            status=ExpenseStatus.SUBMITTED,
            **form.cleaned_data,
        )
        record_audit_event(action="expense.submitted", actor=request.user, organization=request.organization, target=expense, request=request)
        messages.success(request, "Expense submitted for approval.")
        return redirect("module-overview", module="expenses")
    return render(request, "expenses/create.html", {"form": form})


@login_required
@organization_owner_required
@require_POST
def expense_approve(request, expense_id):
    expense = get_object_or_404(Expense, id=expense_id, organization=request.organization, status=ExpenseStatus.SUBMITTED)
    expense.status = ExpenseStatus.APPROVED
    expense.approved_by = request.user
    expense.save(update_fields=["status", "approved_by", "updated_at"])
    record_audit_event(action="expense.approved", actor=request.user, organization=request.organization, target=expense, request=request)
    messages.success(request, "Expense approved.")
    return redirect("module-overview", module="expenses")
