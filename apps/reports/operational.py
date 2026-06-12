from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import F, Sum
from django.shortcuts import render
from django.utils import timezone

from apps.expenses.models import Expense, ExpenseStatus
from apps.inventory.models import StockBalance
from apps.operations.models import Payable, Receivable
from apps.sales.models import SaleLine
from apps.organizations.permissions import accessible_branches_for


@login_required
def operational_report(request):
    organization = request.organization
    branches = accessible_branches_for(request.user, organization)
    sale_lines = SaleLine.objects.filter(organization=organization, sale__location__branch__in=branches)
    revenue = sale_lines.aggregate(total=Sum("line_total"))["total"] or Decimal("0")
    cost = sale_lines.aggregate(total=Sum(F("unit_cost") * F("quantity")))["total"] or Decimal("0")
    expenses = Expense.objects.filter(
        organization=organization, branch__in=branches, status__in=(ExpenseStatus.APPROVED, ExpenseStatus.PAID)
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    receivables = Receivable.objects.filter(
        organization=organization, sale__location__branch__in=branches, outstanding_amount__gt=0
    )
    payables = Payable.objects.filter(
        organization=organization, purchase_order__destination__branch__in=branches, outstanding_amount__gt=0
    )
    overdue = receivables.filter(due_on__lt=timezone.localdate()).aggregate(total=Sum("outstanding_amount"))["total"] or Decimal("0")
    context = {
        "revenue": revenue,
        "cost": cost,
        "gross_profit": revenue - cost,
        "expenses": expenses,
        "operational_profit": revenue - cost - expenses,
        "receivable_total": receivables.aggregate(total=Sum("outstanding_amount"))["total"] or Decimal("0"),
        "payable_total": payables.aggregate(total=Sum("outstanding_amount"))["total"] or Decimal("0"),
        "overdue_total": overdue,
        "stock_balances": StockBalance.objects.filter(
            organization=organization, location__branch__in=branches
        ).select_related("product", "location")[:100],
    }
    return render(request, "reports/operational.html", context)
