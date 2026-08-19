from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.decorators import login_required
from django.db.models import F, Sum
from django.shortcuts import render
from django.utils import timezone

from apps.commissions.models import CommissionAccrual
from apps.integrations.models import IntegrationEvent, IntegrationStatus
from apps.inventory.models import SerialStatus, StockBalance, StockUnit
from apps.operations.models import Receivable
from apps.organizations.permissions import accessible_branches_for, organization_owner_required
from apps.payments.models import Payment, PaymentStatus
from apps.purchasing.models import PurchaseDiscrepancy, PurchaseDiscrepancyStatus
from apps.transfers.models import DiscrepancyStatus, TransferDiscrepancy
from .pagination import paginate_section


@login_required
@organization_owner_required
def exception_report(request):
    organization = request.organization
    branches = accessible_branches_for(request.user, organization)
    today = timezone.localdate()
    receivables = Receivable.objects.filter(
        organization=organization,
        sale__location__branch__in=branches,
        outstanding_amount__gt=0,
    )
    section_queries = {
        "low_stock": StockBalance.objects.filter(
            organization=organization,
            location__branch__in=branches,
            quantity__lte=F("product__reorder_level"),
        ).select_related("product", "location").order_by("product__name", "location__name"),
        "overdue_receivables": receivables.filter(due_on__lt=today).select_related(
            "customer", "sale"
        ).order_by("due_on", "created_at"),
        "purchase_discrepancies": PurchaseDiscrepancy.objects.filter(
            organization=organization,
            line__order__destination__branch__in=branches,
            status=PurchaseDiscrepancyStatus.PENDING,
        ).select_related("line", "line__order").order_by("created_at"),
        "transfer_discrepancies": TransferDiscrepancy.objects.filter(
            organization=organization,
            transfer__destination__branch__in=branches,
            status=DiscrepancyStatus.PENDING,
        ).select_related("transfer").order_by("created_at"),
        "damaged_serials": StockUnit.objects.filter(
            organization=organization,
            location__branch__in=branches,
            status__in=[SerialStatus.DAMAGED, SerialStatus.WARRANTY_REPAIR],
        ).select_related("product", "location").order_by("product__name", "serial_number"),
        "integration_failures": IntegrationEvent.objects.filter(
            organization=organization,
            status__in=[IntegrationStatus.FAILED, IntegrationStatus.DEAD],
        ).order_by("-created_at"),
    }
    section_pages = {}
    section_pagination = {}
    for name, queryset in section_queries.items():
        page, pagination = paginate_section(request, queryset, parameter=f"{name}_page")
        section_pages[name] = page
        section_pagination[name] = pagination
    context = {
        **section_pages,
        "section_pagination": section_pagination,
        "aging_30": receivables.filter(due_on__gte=today - timedelta(days=30), due_on__lt=today).aggregate(total=Sum("outstanding_amount"))["total"] or Decimal("0"),
        "aging_60": receivables.filter(due_on__gte=today - timedelta(days=60), due_on__lt=today - timedelta(days=30)).aggregate(total=Sum("outstanding_amount"))["total"] or Decimal("0"),
        "aging_90_plus": receivables.filter(due_on__lt=today - timedelta(days=60)).aggregate(total=Sum("outstanding_amount"))["total"] or Decimal("0"),
        "commission_liability": CommissionAccrual.objects.filter(
            organization=organization, is_payable=True, paid_at__isnull=True,
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0"),
        "payment_methods": Payment.objects.filter(
            organization=organization,
            sale__location__branch__in=branches,
            status=PaymentStatus.CONFIRMED,
            received_at__date=today,
        ).values("method").annotate(total=Sum("amount")).order_by("method"),
    }
    return render(request, "reports/exceptions.html", context)
