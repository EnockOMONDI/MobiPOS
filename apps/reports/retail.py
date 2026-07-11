import csv
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Count, F, Sum
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.commissions.models import CommissionAccrual
from apps.expenses.models import Expense
from apps.inventory.models import StockUnit
from apps.organizations.permissions import accessible_branches_for
from apps.payments.models import Payment, PaymentStatus
from apps.purchasing.models import PurchaseOrder
from apps.sales.models import Sale, SaleLine


def _date_range_from_request(request):
    today = timezone.localdate()
    start = parse_date(request.GET.get("start", "")) or today.replace(day=1)
    end = parse_date(request.GET.get("end", "")) or today
    if start > end:
        start, end = end, start
    return start, end


@login_required
def retail_analytics_report(request):
    organization = request.organization
    if not organization:
        raise PermissionDenied("An active organization is required.")
    branches = accessible_branches_for(request.user, organization)
    selected_branch_id = request.GET.get("branch", "").strip()
    selected_branch = branches.filter(id=selected_branch_id).first() if selected_branch_id else None
    scoped_branches = branches.filter(id=selected_branch.id) if selected_branch else branches
    start, end = _date_range_from_request(request)

    sales = Sale.objects.filter(
        organization=organization,
        location__branch__in=scoped_branches,
        created_at__date__gte=start,
        created_at__date__lte=end,
    )
    sale_lines = SaleLine.objects.filter(
        organization=organization,
        sale__in=sales,
    )
    purchases = PurchaseOrder.objects.filter(
        organization=organization,
        destination__branch__in=scoped_branches,
        ordered_on__gte=start,
        ordered_on__lte=end,
    )
    purchase_value = purchases.aggregate(total=Sum(F("lines__quantity") * F("lines__unit_cost")))["total"] or Decimal("0")
    expenses = Expense.objects.filter(
        organization=organization,
        branch__in=scoped_branches,
        incurred_on__gte=start,
        incurred_on__lte=end,
    )
    commissions = CommissionAccrual.objects.filter(
        organization=organization,
        sale__location__branch__in=scoped_branches,
        sale__created_at__date__gte=start,
        sale__created_at__date__lte=end,
    )
    payments = Payment.objects.filter(
        organization=organization,
        sale__location__branch__in=scoped_branches,
        received_at__date__gte=start,
        received_at__date__lte=end,
        status=PaymentStatus.CONFIRMED,
    )
    stock_units = StockUnit.objects.filter(
        organization=organization,
    ).filter(
        location__branch__in=scoped_branches
    )

    context = {
        "start": start,
        "end": end,
        "branches": branches,
        "selected_branch": selected_branch,
        "summary": {
            "sales_total": sales.aggregate(total=Sum("total"))["total"] or Decimal("0"),
            "sales_count": sales.count(),
            "gross_profit": (sale_lines.aggregate(
                revenue=Sum("line_total"),
                cost=Sum(F("unit_cost") * F("quantity")),
            )["revenue"] or Decimal("0")) - (sale_lines.aggregate(cost=Sum(F("unit_cost") * F("quantity")))["cost"] or Decimal("0")),
            "purchase_value": purchase_value,
            "expense_total": expenses.aggregate(total=Sum("amount"))["total"] or Decimal("0"),
            "commission_total": commissions.aggregate(total=Sum("amount"))["total"] or Decimal("0"),
        },
        "sales_by_branch": sales.values(
            "location__branch__name"
        ).annotate(count=Count("id"), total=Sum("total")).order_by("-total"),
        "sales_by_agent": sales.values(
            "agent__first_name", "agent__last_name", "agent__username"
        ).annotate(count=Count("id"), total=Sum("total")).order_by("-total"),
        "payment_methods": payments.values("method").annotate(total=Sum("amount"), count=Count("id")).order_by("-total"),
        "purchases_by_supplier": purchases.values(
            "supplier__name"
        ).annotate(count=Count("id"), total=Sum(F("lines__quantity") * F("lines__unit_cost"))).order_by("-total"),
        "expenses_by_category": expenses.values("category").annotate(count=Count("id"), total=Sum("amount")).order_by("-total"),
        "commissions_by_agent": commissions.values(
            "agent__first_name", "agent__last_name", "agent__username"
        ).annotate(count=Count("id"), total=Sum("amount")).order_by("-total"),
        "serial_status": stock_units.values("status").annotate(count=Count("id")).order_by("status"),
        "recent_sales": sales.select_related("customer", "agent", "location__branch").order_by("-created_at")[:20],
    }

    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="retail-analytics.csv"'
        writer = csv.writer(response)
        writer.writerow(["Metric", "Value"])
        for label, value in context["summary"].items():
            writer.writerow([label.replace("_", " ").title(), value])
        writer.writerow([])
        writer.writerow(["Sales By Branch", "Count", "Total"])
        for row in context["sales_by_branch"]:
            writer.writerow([row["location__branch__name"], row["count"], row["total"]])
        return response

    return render(request, "reports/retail_analytics.html", context)
