import csv
from datetime import timedelta
from functools import reduce

from django.conf import settings
from django.core.paginator import Paginator
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Q, Sum
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.organizations.permissions import accessible_branches_for, organization_permission_required
from apps.payments.models import Payment
from apps.expenses.models import Expense
from apps.operations.models import Payable
from apps.purchasing.models import PurchaseOrder
from apps.sales.models import Sale, SaleStatus
from apps.pos.models import POSSession
from apps.sales.models import SaleReturn
from apps.payments.models import Refund
from apps.inventory.models import StockBalance, StockMovement, StockUnit
from apps.purchasing.models import PurchaseDiscrepancy, SupplierReturn
from apps.commissions.models import CommissionAccrual, CommissionPayout
from apps.repairs.models import RepairTicket
from apps.transfers.models import StockTransfer
from apps.organizations.models import AgentProfile
from apps.organizations.permissions import user_has_organization_permission
from .exporting import build_simple_pdf, safe_csv_row
from .models import ReportExport


def _date_range(request):
    start_value = request.GET.get("date_from", "").strip()
    end_value = request.GET.get("date_to", "").strip()
    start = parse_date(start_value) if start_value else None
    end = parse_date(end_value) if end_value else None
    errors = []
    if start_value and not start:
        errors.append("Enter a valid start date.")
    if end_value and not end:
        errors.append("Enter a valid end date.")
    if start and end and start > end:
        errors.append("The start date must be on or before the end date.")
    return start_value, end_value, start, end, errors


def _workspace_context(*, title, eyebrow, description, columns, rows, page_obj, metrics,
                       query, status, date_from, date_to, errors, create_url, create_label,
                       export_module, export_url=None):
    return {
        "title": title,
        "eyebrow": eyebrow,
        "description": description,
        "columns": columns,
        "rows": rows,
        "page_obj": page_obj,
        "metrics": metrics,
        "query": query,
        "status": status,
        "date_from": date_from,
        "date_to": date_to,
        "errors": errors,
        "create_url": create_url,
        "create_label": create_label,
        "export_module": export_module,
        "export_url": export_url,
    }


@login_required
@organization_permission_required("sales.view_sale")
def sales_workspace(request):
    branches = accessible_branches_for(request.user, request.organization)
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    date_from, date_to, start, end, errors = _date_range(request)
    sales = Sale.objects.filter(
        organization=request.organization,
        location__branch__in=branches,
    ).select_related("customer", "location", "location__branch", "created_by").order_by("-created_at")
    if query:
        sales = sales.filter(
            Q(number__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(customer__phone_number__icontains=query)
            | Q(location__name__icontains=query)
        )
    if status in SaleStatus.values:
        sales = sales.filter(status=status)
    if start:
        sales = sales.filter(completed_at__date__gte=start)
    if end:
        sales = sales.filter(completed_at__date__lte=end)
    if errors:
        sales = Sale.objects.none()
    page_obj = Paginator(sales, 25).get_page(request.GET.get("page"))
    total = sales.aggregate(total=Sum("total"))["total"] or 0
    paid = sales.aggregate(total=Sum("paid_total"))["total"] or 0
    rows = [{
        "cells": [
            sale.number,
            sale.completed_at.strftime("%d %b %Y") if sale.completed_at else "Draft",
            sale.customer.name if sale.customer else "Walk-in customer",
            f"{sale.location.branch.name} / {sale.location.name}",
            sale.get_status_display(),
            f"KES {sale.total:,.2f}",
            f"KES {sale.paid_total:,.2f}",
            f"KES {sale.balance_due:,.2f}",
        ],
        "detail_url": f"/sales/{sale.id}/",
    } for sale in page_obj.object_list]
    return render(request, "reports/decision_workspace.html", _workspace_context(
        title="Sales register", eyebrow="Sales & POS",
        description="See what was sold, what has been paid, and what still needs attention.",
        columns=["Sale", "Date", "Customer", "Branch / location", "Status", "Sale value", "Paid", "Balance"],
        rows=rows, page_obj=page_obj,
        metrics=[("Sales value", f"KES {total:,.2f}"), ("Collected", f"KES {paid:,.2f}"),
                 ("Outstanding", f"KES {total - paid:,.2f}"), ("Transactions", sales.count())],
        query=query, status=status, date_from=date_from, date_to=date_to, errors=errors,
        create_url="/pos/cart/", create_label="New sale", export_module="sales",
    ))


@login_required
@organization_permission_required("payments.view_payment")
def payments_workspace(request):
    branches = accessible_branches_for(request.user, request.organization)
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    method = request.GET.get("method", "").strip()
    date_from, date_to, start, end, errors = _date_range(request)
    payments = Payment.objects.filter(
        organization=request.organization,
        sale__location__branch__in=branches,
    ).select_related("sale", "customer", "sale__location", "sale__location__branch", "received_by").order_by("-received_at")
    if query:
        payments = payments.filter(
            Q(number__icontains=query) | Q(provider_reference__icontains=query)
            | Q(customer__name__icontains=query) | Q(sale__number__icontains=query)
        )
    if status:
        payments = payments.filter(status=status)
    if method:
        payments = payments.filter(method=method)
    if start:
        payments = payments.filter(received_at__date__gte=start)
    if end:
        payments = payments.filter(received_at__date__lte=end)
    if errors:
        payments = Payment.objects.none()
    page_obj = Paginator(payments, 25).get_page(request.GET.get("page"))
    total = payments.aggregate(total=Sum("amount"))["total"] or 0
    confirmed = payments.filter(status="confirmed").aggregate(total=Sum("amount"))["total"] or 0
    rows = [{
        "cells": [
            payment.number,
            payment.received_at.strftime("%d %b %Y %H:%M"),
            payment.sale.number if payment.sale else "—",
            payment.customer.name if payment.customer else "Walk-in customer",
            payment.get_method_display(),
            payment.get_status_display(),
            f"KES {payment.amount:,.2f}",
            payment.provider_reference or "—",
        ],
        "detail_url": f"/sales/{payment.sale_id}/" if payment.sale_id else "",
    } for payment in page_obj.object_list]
    return render(request, "reports/decision_workspace.html", _workspace_context(
        title="Payments register", eyebrow="Sales & POS",
        description="Review every payment by method, confirmation status and reference.",
        columns=["Payment", "Received", "Sale", "Customer", "Method", "Status", "Amount", "Reference"],
        rows=rows, page_obj=page_obj,
        metrics=[("Recorded", f"KES {total:,.2f}"), ("Confirmed", f"KES {confirmed:,.2f}"),
                 ("Awaiting review", payments.filter(status="pending").count()), ("Payments", payments.count())],
        query=query, status=status, date_from=date_from, date_to=date_to, errors=errors,
        create_url="", create_label="", export_module="payments",
    ))


@login_required
@organization_permission_required("purchasing.view_purchaseorder")
def purchasing_workspace(request):
    branches = accessible_branches_for(request.user, request.organization)
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    date_from, date_to, start, end, errors = _date_range(request)
    purchases = PurchaseOrder.objects.filter(
        organization=request.organization,
        destination__branch__in=branches,
    ).select_related("supplier", "destination", "destination__branch", "created_by").prefetch_related("lines").order_by("-ordered_on", "-created_at")
    if query:
        purchases = purchases.filter(
            Q(number__icontains=query) | Q(supplier__name__icontains=query)
            | Q(supplier_reference__icontains=query) | Q(destination__name__icontains=query)
        )
    if status:
        purchases = purchases.filter(status=status)
    if start:
        purchases = purchases.filter(ordered_on__gte=start)
    if end:
        purchases = purchases.filter(ordered_on__lte=end)
    if errors:
        purchases = PurchaseOrder.objects.none()
    page_obj = Paginator(purchases, 25).get_page(request.GET.get("page"))
    total = sum((sum((line.quantity * line.unit_cost for line in purchase.lines.all()), 0) for purchase in page_obj.object_list), 0)
    rows = []
    for purchase in page_obj.object_list:
        purchase_total = sum((line.quantity * line.unit_cost for line in purchase.lines.all()), 0)
        rows.append({
            "cells": [
                purchase.number, purchase.ordered_on.strftime("%d %b %Y"), purchase.supplier.name,
                f"{purchase.destination.branch.name} / {purchase.destination.name}",
                purchase.get_status_display(), f"KES {purchase_total:,.2f}",
                purchase.extraction_status.replace("_", " ").title(),
            ],
            "detail_url": f"/purchases/{purchase.id}/",
    })
    return render(request, "reports/decision_workspace.html", _workspace_context(
        title="Purchasing register", eyebrow="Purchasing",
        description="Track supplier orders from request through receiving and discrepancy resolution.",
        columns=["Purchase", "Ordered", "Supplier", "Destination", "Status", "Order value", "Document review"],
        rows=rows, page_obj=page_obj,
        metrics=[("Order value", f"KES {total:,.2f}"), ("Open orders", purchases.exclude(status__in=["closed", "cancelled"]).count()),
                 ("Awaiting receipt", purchases.filter(status__in=["approved", "part_received"]).count()), ("Orders", purchases.count())],
        query=query, status=status, date_from=date_from, date_to=date_to, errors=errors,
        create_url="/purchases/new/", create_label="New purchase", export_module="purchases",
    ))


REGISTER_CONFIG = {
    "sessions": {
        "title": "Cashier sessions", "eyebrow": "Sales & POS",
        "description": "Open, closed and reviewed tills with the cash difference visible before close-out.",
        "model": POSSession, "permission": "pos.view_possession", "date_field": "opened_at",
        "search": ("number", "cashier__first_name", "cashier__last_name", "location__name"),
        "columns": ["Session", "Opened", "Cashier", "Branch / location", "Status", "Expected", "Actual", "Variance"],
        "fields": lambda obj: [obj.number, obj.opened_at.strftime("%d %b %Y %H:%M"), obj.cashier.get_full_name() or obj.cashier.get_username(), f"{obj.location.branch.name} / {obj.location.name}", obj.get_status_display(), f"KES {obj.expected_cash:,.2f}", f"KES {obj.actual_cash or 0:,.2f}", f"KES {obj.variance:,.2f}"],
        "scope": lambda qs, branches: qs.filter(location__branch__in=branches), "detail": lambda obj: "",
        "select_related": ("cashier", "location", "location__branch"),
    },
    "returns": {
        "title": "Returns and refunds", "eyebrow": "Sales & POS",
        "description": "Follow every return from request to approved outcome without losing the original sale.",
        "model": SaleReturn, "permission": "sales.view_salereturn", "date_field": "created_at",
        "search": ("number", "sale__number", "sale__customer__name"),
        "columns": ["Return", "Requested", "Sale", "Customer", "Status", "Outcome", "Refund"],
        "fields": lambda obj: [obj.number, obj.created_at.strftime("%d %b %Y"), obj.sale.number, obj.sale.customer.name if obj.sale.customer else "Walk-in customer", obj.get_status_display(), obj.get_outcome_display(), f"KES {obj.refund_amount:,.2f}"],
        "scope": lambda qs, branches: qs.filter(sale__location__branch__in=branches), "detail": lambda obj: f"/sales/{obj.sale_id}/",
        "select_related": ("sale", "sale__customer", "sale__location", "sale__location__branch"),
    },
    "refunds": {
        "title": "Refund register", "eyebrow": "Sales & POS",
        "description": "Review money returned to customers, the original payment and the approval status.",
        "model": Refund, "permission": "payments.view_refund", "date_field": "created_at",
        "search": ("number", "payment__number", "payment__provider_reference", "reason"),
        "columns": ["Refund", "Requested", "Payment", "Customer", "Status", "Amount", "Reason"],
        "fields": lambda obj: [obj.number, obj.created_at.strftime("%d %b %Y"), obj.payment.number,
                               obj.payment.customer.name if obj.payment.customer_id else "Walk-in customer",
                               obj.get_status_display(), f"KES {obj.amount:,.2f}", obj.reason],
        "scope": lambda qs, branches: qs.filter(payment__sale__location__branch__in=branches),
        "detail": lambda obj: reverse("sale-detail", args=[obj.payment.sale_id]) if obj.payment.sale_id else "",
        "select_related": ("payment", "payment__customer", "payment__sale", "payment__sale__location", "payment__sale__location__branch"),
    },
    "inventory": {
        "title": "Serialized inventory", "eyebrow": "Inventory",
        "description": "Find each device by identity, status and current location before it is sold or moved.",
        "model": StockUnit, "permission": "inventory.view_stockunit", "date_field": "created_at",
        "search": ("serial_number", "secondary_serial", "product__name", "location__name"),
        "columns": ["IMEI / serial", "Product", "Location", "Status", "Added"],
        "fields": lambda obj: [obj.serial_number, obj.product.name, f"{obj.location.branch.name} / {obj.location.name}", obj.get_status_display(), obj.created_at.strftime("%d %b %Y")],
        "scope": lambda qs, branches: qs.filter(location__branch__in=branches), "detail": lambda obj: "",
        "select_related": ("product", "location", "location__branch"),
    },
    "stock": {
        "title": "Stock levels", "eyebrow": "Inventory",
        "description": "See available quantity and stock value by product and stock-holding location.",
        "model": StockBalance, "permission": "inventory.view_stockbalance", "date_field": "updated_at",
        "search": ("product__name", "product__sku", "location__name", "location__branch__name"),
        "columns": ["Product", "SKU", "Branch / location", "Quantity", "Unit cost", "Stock value"],
        "fields": lambda obj: [obj.product.name, obj.product.sku,
                               f"{obj.location.branch.name} / {obj.location.name}",
                               f"{obj.quantity:,.3f}", f"KES {obj.product.cost_price:,.2f}",
                               f"KES {(obj.quantity * obj.product.cost_price):,.2f}"],
        "scope": lambda qs, branches: qs.filter(location__branch__in=branches), "detail": lambda obj: "",
        "select_related": ("product", "location", "location__branch"),
    },
    "agent-stock": {
        "title": "Agent stock custody", "eyebrow": "Inventory",
        "description": "See which serialized devices are assigned to agents and what remains to be accounted for.",
        "model": StockUnit, "permission": "inventory.view_stockunit", "date_field": "created_at",
        "search": ("serial_number", "secondary_serial", "product__name", "location__name"),
        "columns": ["IMEI / serial", "Product", "Custody location", "Status", "Added"],
        "fields": lambda obj: [obj.serial_number, obj.product.name, obj.location.name, obj.get_status_display(), obj.created_at.strftime("%d %b %Y")],
        "scope": lambda qs, branches: qs.filter(location__branch__in=branches, location__location_type="agent"), "detail": lambda obj: "",
        "select_related": ("product", "location", "location__branch"),
    },
    "movements": {
        "title": "Stock movements", "eyebrow": "Inventory",
        "description": "A chronological ledger of stock entering, leaving and changing location.",
        "model": StockMovement, "permission": "inventory.view_stockmovement", "date_field": "created_at",
        "search": ("movement_type", "product__name", "reason", "location__name"),
        "columns": ["Movement", "Date", "Product", "Location", "Quantity", "Reason"],
        "fields": lambda obj: [obj.get_movement_type_display(), obj.created_at.strftime("%d %b %Y %H:%M"), obj.product.name, obj.location.name, f"{obj.quantity:,.3f}", obj.reason or "—"],
        "scope": lambda qs, branches: qs.filter(location__branch__in=branches), "detail": lambda obj: "",
        "select_related": ("product", "location", "location__branch"),
    },
    "transfers": {
        "title": "Device transfers", "eyebrow": "Inventory",
        "description": "Track dispatch, transit, receipt and discrepancies between stock-holding locations.",
        "model": StockTransfer, "permission": "transfers.view_stocktransfer", "date_field": "created_at",
        "search": ("number", "source__name", "destination__name", "requested_by__first_name", "requested_by__last_name"),
        "columns": ["Transfer", "Requested", "From", "To", "Status", "Requested by"],
        "fields": lambda obj: [obj.number, obj.created_at.strftime("%d %b %Y"), obj.source.name, obj.destination.name, obj.get_status_display(), obj.requested_by.get_full_name() or obj.requested_by.get_username()],
        "scope": lambda qs, branches: qs.filter(Q(source__branch__in=branches) | Q(destination__branch__in=branches)), "detail": lambda obj: reverse("transfer-detail", args=[obj.id]),
        "select_related": ("source", "source__branch", "destination", "destination__branch", "requested_by"),
    },
    "repairs": {
        "title": "Repair register", "eyebrow": "Repairs & warranty",
        "description": "Manage customer devices through diagnosis, repair, quality checks and collection.",
        "model": RepairTicket, "permission": "repairs.view_repairticket", "date_field": "created_at",
        "search": ("number", "customer__name", "customer__phone_number", "stock_unit__serial_number"),
        "columns": ["Ticket", "Opened", "Customer", "Device", "Status", "Amount due"],
        "fields": lambda obj: [obj.number, obj.created_at.strftime("%d %b %Y"), obj.customer.name, obj.stock_unit.serial_number if obj.stock_unit else "—", obj.get_status_display(), f"KES {obj.outstanding_amount:,.2f}"],
        "scope": lambda qs, branches: qs.filter(branch__in=branches), "detail": lambda obj: reverse("repair-detail", args=[obj.id]),
        "select_related": ("customer", "stock_unit", "branch"),
    },
    "purchase-discrepancies": {
        "title": "Purchase discrepancies", "eyebrow": "Purchasing",
        "description": "Resolve shortages and damaged goods against the supplier order before payment is settled.",
        "model": PurchaseDiscrepancy, "permission": "purchasing.view_purchasediscrepancy", "date_field": "created_at",
        "search": ("number", "line__order__number", "line__order__supplier__name", "reason"),
        "columns": ["Case", "Opened", "Purchase", "Supplier", "Difference", "Status"],
        "fields": lambda obj: [obj.number, obj.created_at.strftime("%d %b %Y"), obj.line.order.number, obj.line.order.supplier.name, f"{obj.expected_quantity - obj.accepted_quantity:,.3f}", obj.get_status_display()],
        "scope": lambda qs, branches: qs.filter(line__order__destination__branch__in=branches), "detail": lambda obj: reverse("purchase-detail", args=[obj.line.order_id]),
        "select_related": ("line", "line__order", "line__order__supplier", "line__product", "line__order__destination", "line__order__destination__branch"),
    },
    "supplier-returns": {
        "title": "Supplier returns", "eyebrow": "Purchasing",
        "description": "Track goods sent back to suppliers and the credit expected from each return.",
        "model": SupplierReturn, "permission": "purchasing.view_supplierreturn", "date_field": "created_at",
        "search": ("number", "line__order__supplier__name", "reason"),
        "columns": ["Return", "Requested", "Supplier", "Product", "Status", "Credit"],
        "fields": lambda obj: [obj.number, obj.created_at.strftime("%d %b %Y"), obj.line.order.supplier.name, obj.line.product.name, obj.get_status_display(), f"KES {obj.credit_amount:,.2f}"],
        "scope": lambda qs, branches: qs.filter(line__order__destination__branch__in=branches), "detail": lambda obj: reverse("purchase-detail", args=[obj.line.order_id]),
        "select_related": ("line", "line__order", "line__order__supplier", "line__product", "line__order__destination", "line__order__destination__branch"),
    },
    "commissions": {
        "title": "Commission accruals", "eyebrow": "Customers & finance",
        "description": "Review earned commission, payment eligibility and the sale behind each amount.",
        "model": CommissionAccrual, "permission": "commissions.view_commissionaccrual", "date_field": "created_at",
        "search": ("sale__number", "agent__first_name", "agent__last_name", "rule__name"),
        "columns": ["Sale", "Recorded", "Agent", "Rule", "Amount", "Payable"],
        "fields": lambda obj: [obj.sale.number, obj.created_at.strftime("%d %b %Y"), obj.agent.get_full_name() or obj.agent.get_username(), obj.rule.name, f"KES {obj.amount:,.2f}", "Yes" if obj.is_payable else "Not yet"],
        "scope": lambda qs, branches: qs.filter(sale__location__branch__in=branches), "detail": lambda obj: reverse("sale-detail", args=[obj.sale_id]),
        "select_related": ("sale", "sale__location", "sale__location__branch", "agent", "rule"),
    },
    "commission-payouts": {
        "title": "Commission payouts", "eyebrow": "Customers & finance",
        "description": "Track commission payments from request through approval and settlement.",
        "model": CommissionPayout, "permission": "commissions.view_commissionpayout", "date_field": "created_at",
        "search": ("number", "agent__first_name", "agent__last_name", "payment_reference"),
        "columns": ["Payout", "Period", "Agent", "Status", "Amount", "Payment reference"],
        "fields": lambda obj: [obj.number, f"{obj.period_start:%d %b %Y} - {obj.period_end:%d %b %Y}",
                               obj.agent.get_full_name() or obj.agent.get_username(), obj.get_status_display(),
                               f"KES {obj.amount:,.2f}", obj.payment_reference or "—"],
        "scope": lambda qs, branches: qs, "detail": lambda obj: "",
        "select_related": ("agent", "requested_by", "approved_by"),
    },
}


def _operational_queryset(request, config):
    """Build the filtered queryset shared by the page and its export."""
    model = config["model"]
    branches = accessible_branches_for(request.user, request.organization)
    queryset = config["scope"](
        model.objects.filter(organization=request.organization), branches
    )
    if config.get("select_related"):
        queryset = queryset.select_related(*config["select_related"])
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    date_from, date_to, start, end, errors = _date_range(request)
    if query:
        search_query = reduce(
            lambda combined, field: combined | Q(**{f"{field}__icontains": query}),
            config["search"],
            Q(),
        )
        queryset = queryset.filter(search_query)
    has_status = any(field.name == "status" for field in model._meta.fields)
    if status and has_status:
        queryset = queryset.filter(status=status)
    date_field = config["date_field"]
    if start:
        queryset = queryset.filter(**({f"{date_field}__date__gte": start} if config.get("date_is_datetime") else {f"{date_field}__gte": start}))
    if end:
        queryset = queryset.filter(**({f"{date_field}__date__lte": end} if config.get("date_is_datetime") else {f"{date_field}__lte": end}))
    if errors:
        queryset = model.objects.none()
    return queryset.order_by(f"-{date_field}"), query, status, date_from, date_to, errors, has_status


def _visible_column_indexes(request, config):
    """Return valid requested column positions; an empty selection means all."""
    raw_values = request.GET.getlist("columns")
    if not raw_values:
        return list(range(len(config["columns"])))
    values = []
    for raw in raw_values:
        values.extend(raw.split(","))
    indexes = []
    for value in values:
        try:
            index = int(value.strip())
        except ValueError:
            continue
        if 0 <= index < len(config["columns"]) and index not in indexes:
            indexes.append(index)
    return indexes or list(range(len(config["columns"])))


def operational_register_export(request, register):
    """Render a complete export using the same config and filters as the workspace."""
    config = REGISTER_CONFIG.get(register)
    if not config:
        raise ValueError(f"Unknown operational register: {register}")
    queryset, *_ = _operational_queryset(request, config)
    visible_indexes = _visible_column_indexes(request, config)
    headers = [config["columns"][index] for index in visible_indexes]
    rows = (
        [values[index] for index in visible_indexes]
        for values in (config["fields"](obj) for obj in queryset.iterator(chunk_size=500))
    )
    requested_format = request.GET.get("format", "csv")
    if requested_format == "pdf":
        content = build_simple_pdf(
            title=config["title"], headers=headers, rows=rows
        )
        response = HttpResponse(content, content_type="application/pdf")
        extension = "pdf"
    else:
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        writer = csv.writer(response)
        writer.writerow(safe_csv_row(headers))
        for row in rows:
            writer.writerow(safe_csv_row(row))
        extension = "csv"
    response["Content-Disposition"] = f'attachment; filename="{register}-{timezone.localdate().isoformat()}.{extension}"'
    return response


@login_required
def operational_register(request, register):
    config = REGISTER_CONFIG.get(register)
    if not config or not request.organization or not user_has_organization_permission(request.user, request.organization, config["permission"]):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    model = config["model"]
    queryset, query, status, date_from, date_to, errors, has_status = _operational_queryset(request, config)
    requested_format = request.GET.get("format", "")
    if requested_format in {"csv", "pdf"}:
        row_count = queryset.count()
        if request.GET.get("delivery") == "background" or row_count >= settings.REPORT_ASYNC_EXPORT_THRESHOLD:
            export_filters = {}
            for key, values in request.GET.lists():
                if key in {"format", "delivery", "page"}:
                    continue
                export_filters[key] = values[0] if len(values) == 1 else ",".join(values)
            export = ReportExport.objects.create(
                organization=request.organization,
                requested_by=request.user,
                module=register,
                export_format=requested_format,
                filters=export_filters,
                row_count=row_count,
                expires_at=timezone.now() + timedelta(days=settings.REPORT_EXPORT_RETENTION_DAYS),
            )
            from .tasks import generate_report_export

            transaction.on_commit(lambda: generate_report_export.delay(str(export.id)))
            return redirect("report-export-detail", export_id=export.id)
        return operational_register_export(request, register)
    visible_indexes = _visible_column_indexes(request, config)
    visible_columns = [config["columns"][index] for index in visible_indexes]
    page_obj = Paginator(queryset, 25).get_page(request.GET.get("page"))
    rows = [
        {"cells": [values[index] for index in visible_indexes], "detail_url": config["detail"](obj)}
        for obj in page_obj.object_list
        for values in [config["fields"](obj)]
    ]
    return render(request, "reports/decision_workspace.html", _workspace_context(
        title=config["title"], eyebrow=config["eyebrow"], description=config["description"],
        columns=visible_columns, rows=rows, page_obj=page_obj,
        metrics=[("Visible records", queryset.count()), ("Needs attention", queryset.filter(status__in=["pending", "requested", "in_transit"]).count() if has_status else "—")],
        query=query, status=status, date_from=date_from, date_to=date_to, errors=errors,
        create_url="", create_label="", export_module=register,
        export_url=reverse("operational-register", args=[register]),
    ) | {
        "column_options": list(enumerate(config["columns"])),
        "selected_columns": visible_indexes,
    })
@login_required
@organization_permission_required("expenses.view_expense")
def expenses_workspace(request):
    branches = accessible_branches_for(request.user, request.organization)
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    date_from, date_to, start, end, errors = _date_range(request)
    expenses = Expense.objects.filter(organization=request.organization, branch__in=branches).select_related(
        "branch", "requested_by", "approved_by"
    ).order_by("-incurred_on", "-created_at")
    if query:
        expenses = expenses.filter(Q(number__icontains=query) | Q(category__icontains=query) | Q(description__icontains=query))
    if status:
        expenses = expenses.filter(status=status)
    if start:
        expenses = expenses.filter(incurred_on__gte=start)
    if end:
        expenses = expenses.filter(incurred_on__lte=end)
    if errors:
        expenses = Expense.objects.none()
    page_obj = Paginator(expenses, 25).get_page(request.GET.get("page"))
    total = expenses.aggregate(total=Sum("amount"))["total"] or 0
    rows = [{
        "cells": [expense.number, expense.incurred_on.strftime("%d %b %Y"), expense.category, expense.branch.name,
                  expense.get_status_display(), f"KES {expense.amount:,.2f}", expense.requested_by.get_full_name() or expense.requested_by.get_username()],
        "detail_url": f"/expenses/{expense.id}/",
    } for expense in page_obj.object_list]
    return render(request, "reports/decision_workspace.html", _workspace_context(
        title="Expense decisions", eyebrow="Customers & Finance",
        description="Review branch expenses, approvals and payments before they affect cash flow.",
        columns=["Expense", "Date", "Category", "Branch", "Status", "Amount", "Requested by"],
        rows=rows, page_obj=page_obj,
        metrics=[("Total value", f"KES {total:,.2f}"), ("Awaiting approval", expenses.filter(status="submitted").count()),
                 ("Approved", expenses.filter(status="approved").count()), ("Expenses", expenses.count())],
        query=query, status=status, date_from=date_from, date_to=date_to, errors=errors,
        create_url="/expenses/new/", create_label="New expense", export_module="expenses",
    ))


@login_required
@organization_permission_required("purchasing.view_purchaseorder")
def payables_workspace(request):
    branches = accessible_branches_for(request.user, request.organization)
    query = request.GET.get("q", "").strip()
    date_from, date_to, start, end, errors = _date_range(request)
    payables = Payable.objects.filter(
        organization=request.organization, purchase_order__destination__branch__in=branches,
    ).select_related("supplier", "purchase_order", "purchase_order__destination", "purchase_order__destination__branch").order_by("due_on", "-created_at")
    if query:
        payables = payables.filter(Q(supplier__name__icontains=query) | Q(purchase_order__number__icontains=query))
    if start:
        payables = payables.filter(due_on__gte=start)
    if end:
        payables = payables.filter(due_on__lte=end)
    if errors:
        payables = Payable.objects.none()
    page_obj = Paginator(payables, 25).get_page(request.GET.get("page"))
    outstanding = payables.aggregate(total=Sum("outstanding_amount"))["total"] or 0
    rows = [{
        "cells": [payable.purchase_order.number, payable.supplier.name, payable.purchase_order.destination.branch.name,
                  payable.due_on.strftime("%d %b %Y"), "Settled" if payable.is_settled else "Outstanding",
                  f"KES {payable.original_amount:,.2f}", f"KES {payable.outstanding_amount:,.2f}"],
        "detail_url": f"/payables/{payable.id}/",
    } for payable in page_obj.object_list]
    return render(request, "reports/decision_workspace.html", _workspace_context(
        title="Supplier balances", eyebrow="Purchasing",
        description="See what is owed to each supplier and open the purchase record before making a payment.",
        columns=["Purchase", "Supplier", "Branch", "Due date", "Status", "Original", "Outstanding"],
        rows=rows, page_obj=page_obj,
        metrics=[("Outstanding", f"KES {outstanding:,.2f}"), ("Due records", payables.filter(is_settled=False).count()),
                 ("Settled", payables.filter(is_settled=True).count()), ("Supplier balances", payables.count())],
        query=query, status="", date_from=date_from, date_to=date_to, errors=errors,
        create_url="/purchases/new/", create_label="New purchase", export_module="payables",
    ))
