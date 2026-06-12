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
    context = {
        "low_stock": StockBalance.objects.filter(
            organization=organization,
            location__branch__in=branches,
            quantity__lte=F("product__reorder_level"),
        ).select_related("product", "location")[:100],
        "aged_serials": StockUnit.objects.filter(
            organization=organization,
            location__branch__in=branches,
            status=SerialStatus.AVAILABLE,
            created_at__lt=timezone.now() - timedelta(days=90),
        ).select_related("product", "location")[:100],
        "damaged_serials": StockUnit.objects.filter(
            organization=organization,
            location__branch__in=branches,
            status__in=[SerialStatus.DAMAGED, SerialStatus.WARRANTY_REPAIR],
        ).select_related("product", "location")[:100],
        "overdue_receivables": receivables.filter(due_on__lt=today).select_related("customer", "sale")[:100],
        "aging_30": receivables.filter(due_on__gte=today - timedelta(days=30), due_on__lt=today).aggregate(total=Sum("outstanding_amount"))["total"] or Decimal("0"),
        "aging_60": receivables.filter(due_on__gte=today - timedelta(days=60), due_on__lt=today - timedelta(days=30)).aggregate(total=Sum("outstanding_amount"))["total"] or Decimal("0"),
        "aging_90_plus": receivables.filter(due_on__lt=today - timedelta(days=60)).aggregate(total=Sum("outstanding_amount"))["total"] or Decimal("0"),
        "purchase_discrepancies": PurchaseDiscrepancy.objects.filter(
            organization=organization,
            line__order__destination__branch__in=branches,
            status=PurchaseDiscrepancyStatus.PENDING,
        )[:100],
        "transfer_discrepancies": TransferDiscrepancy.objects.filter(
            organization=organization,
            transfer__destination__branch__in=branches,
            status=DiscrepancyStatus.PENDING,
        )[:100],
        "integration_failures": IntegrationEvent.objects.filter(
            organization=organization,
            status__in=[IntegrationStatus.FAILED, IntegrationStatus.DEAD],
        )[:100],
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
