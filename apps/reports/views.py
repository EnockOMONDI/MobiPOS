import csv

from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import Http404, HttpResponse
from django.core.exceptions import PermissionDenied
from django.shortcuts import render

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.contacts.models import Contact
from apps.commissions.models import CommissionAccrual, CommissionPayout
from apps.expenses.models import Expense
from apps.integrations.models import IntegrationEvent
from apps.inventory.models import StockAdjustment, StockBalance, StockMovement, StockUnit
from apps.operations.models import ApprovalRequest, Payable, Receivable
from apps.organizations.models import Branch, Location, Membership, Organization, Role, SubscriptionInvoice
from apps.organizations.permissions import accessible_branches_for
from apps.payments.models import Payment, Refund
from apps.pos.models import POSSession
from apps.purchasing.models import PurchaseDiscrepancy, PurchaseOrder, SupplierReturn
from apps.repairs.models import RepairTicket
from apps.sales.models import Sale, SaleReturn
from apps.transfers.models import StockTransfer


BRANCH_LOOKUPS = {
    Branch: "id",
    Location: "branch",
    StockUnit: "location__branch",
    StockBalance: "location__branch",
    StockMovement: "location__branch",
    StockAdjustment: "location__branch",
    PurchaseOrder: "destination__branch",
    SupplierReturn: "line__order__destination__branch",
    PurchaseDiscrepancy: "line__order__destination__branch",
    Sale: "location__branch",
    Payment: "sale__location__branch",
    Expense: "branch",
    CommissionAccrual: "sale__location__branch",
    RepairTicket: "branch",
    Receivable: "sale__location__branch",
    Payable: "purchase_order__destination__branch",
    SaleReturn: "sale__location__branch",
    Refund: "payment__sale__location__branch",
    POSSession: "location__branch",
}


def _scope_to_user_branches(queryset, model, request):
    branches = accessible_branches_for(request.user, request.organization)
    if model is StockTransfer:
        return queryset.filter(Q(source__branch__in=branches) | Q(destination__branch__in=branches)).distinct()
    lookup = BRANCH_LOOKUPS.get(model)
    return queryset.filter(**{f"{lookup}__in": branches}) if lookup else queryset


@login_required
def dashboard(request):
    organization = request.organization
    if organization:
        branches = accessible_branches_for(request.user, organization)
        metrics = {
            "organizations": 1,
            "branches": branches.count(),
            "active_users": Membership.objects.filter(organization=organization, status="active").count(),
            "pending_approvals": ApprovalRequest.objects.filter(organization=organization, status="pending").count(),
            "sales_total": Sale.objects.filter(organization=organization, location__branch__in=branches).aggregate(total=Sum("total"))["total"] or 0,
            "stock_units": StockUnit.objects.filter(organization=organization, location__branch__in=branches, status="available").count(),
            "expenses_total": Expense.objects.filter(organization=organization, branch__in=branches).aggregate(total=Sum("amount"))["total"] or 0,
            "integration_failures": IntegrationEvent.objects.filter(organization=organization, status="failed").count(),
        }
    elif request.user.is_platform_admin or request.user.is_superuser:
        metrics = {
            "organizations": Organization.objects.filter(status="active").count(),
            "branches": Branch.objects.filter(is_active=True).count(),
            "active_users": User.objects.filter(is_active=True).count(),
            "pending_approvals": Organization.objects.filter(status="pending").count(),
            "sales_total": Sale.objects.aggregate(total=Sum("total"))["total"] or 0,
            "stock_units": StockUnit.objects.filter(status="available").count(),
            "expenses_total": Expense.objects.aggregate(total=Sum("amount"))["total"] or 0,
            "integration_failures": IntegrationEvent.objects.filter(status="failed").count(),
        }
    else:
        metrics = dict.fromkeys(
            ("organizations", "branches", "active_users", "pending_approvals", "sales_total", "stock_units", "expenses_total", "integration_failures"),
            0,
        )
    return render(request, "dashboard.html", {"metrics": metrics})


MODULES = {
    "products": ("Products", Product, ("name", "sku", "selling_price", "is_serialized")),
    "contacts": ("Customers and suppliers", Contact, ("name", "contact_type", "phone_number", "credit_limit", "is_active")),
    "inventory": ("Serialized inventory", StockUnit, ("serial_number", "product", "status", "location")),
    "stock": ("Stock balances", StockBalance, ("product", "location", "quantity")),
    "movements": ("Stock movements", StockMovement, ("movement_type", "product", "location", "quantity", "reason")),
    "adjustments": ("Stock adjustments", StockAdjustment, ("number", "product", "location", "quantity", "status")),
    "purchases": ("Purchases", PurchaseOrder, ("number", "supplier", "destination", "status")),
    "supplier-returns": ("Supplier returns", SupplierReturn, ("number", "line", "quantity", "status", "reason")),
    "purchase-discrepancies": ("Purchase discrepancies", PurchaseDiscrepancy, ("number", "line", "damaged_quantity", "missing_quantity", "status")),
    "transfers": ("Stock transfers", StockTransfer, ("number", "source", "destination", "status")),
    "sales": ("Sales", Sale, ("number", "customer", "status", "total", "paid_total")),
    "payments": ("Payments", Payment, ("number", "method", "status", "amount", "provider_reference")),
    "expenses": ("Expenses", Expense, ("number", "category", "status", "amount", "incurred_on")),
    "commissions": ("Commissions", CommissionAccrual, ("agent", "sale", "amount", "is_payable")),
    "commission-payouts": ("Commission payouts", CommissionPayout, ("number", "agent", "period_start", "period_end", "amount", "status")),
    "repairs": ("Repairs", RepairTicket, ("number", "customer", "status", "quoted_amount")),
    "integrations": ("Integration events", IntegrationEvent, ("provider", "event_type", "status", "attempts")),
    "receivables": ("Receivables", Receivable, ("customer", "sale", "outstanding_amount", "due_on")),
    "payables": ("Payables", Payable, ("supplier", "purchase_order", "outstanding_amount", "due_on")),
    "approvals": ("Approval inbox", ApprovalRequest, ("request_type", "target_type", "status", "requested_by")),
    "returns": ("Returns", SaleReturn, ("number", "sale", "status", "refund_amount")),
    "refunds": ("Refunds", Refund, ("number", "payment", "status", "amount")),
    "subscriptions": ("Subscription invoices", SubscriptionInvoice, ("number", "subscription", "status", "amount", "due_on")),
    "sessions": ("Cashier sessions", POSSession, ("number", "location", "cashier", "status", "expected_cash", "variance")),
    "users": ("Organization users", Membership, ("user", "status", "is_owner")),
    "branches": ("Branches", Branch, ("name", "code", "company", "is_active")),
    "locations": ("Locations", Location, ("name", "code", "branch", "location_type", "is_active")),
    "roles": ("Roles", Role, ("name", "code", "description", "is_active")),
}
SENSITIVE_MODULES = {
    "approvals", "branches", "commission-payouts", "integrations", "locations",
    "roles", "subscriptions", "users",
}
MODULE_PERMISSIONS = {
    "products": "catalog.view_product",
    "contacts": "contacts.view_contact",
    "inventory": "inventory.view_stockunit",
    "stock": "inventory.view_stockbalance",
    "movements": "inventory.view_stockmovement",
    "adjustments": "inventory.view_stockadjustment",
    "purchases": "purchasing.view_purchaseorder",
    "supplier-returns": "purchasing.view_supplierreturn",
    "purchase-discrepancies": "purchasing.view_purchasediscrepancy",
    "transfers": "transfers.view_stocktransfer",
    "sales": "sales.view_sale",
    "payments": "payments.view_payment",
    "expenses": "expenses.view_expense",
    "commissions": "commissions.view_commissionaccrual",
    "repairs": "repairs.view_repairticket",
    "receivables": "sales.view_sale",
    "payables": "purchasing.view_purchaseorder",
    "returns": "sales.view_salereturn",
    "refunds": "payments.view_refund",
    "sessions": "pos.view_possession",
}


@login_required
def module_overview(request, module):
    if module not in MODULES:
        raise Http404("Unknown module.")
    if module in SENSITIVE_MODULES and request.organization:
        membership = request.membership
        if not (request.user.is_superuser or request.user.is_platform_admin or (membership and membership.is_owner)):
            raise PermissionDenied("Organization owner access is required.")
    permission = MODULE_PERMISSIONS.get(module)
    if permission and request.organization:
        from apps.organizations.permissions import user_has_organization_permission
        if not user_has_organization_permission(request.user, request.organization, permission):
            raise PermissionDenied(f"Permission {permission} is required.")
    title, model, fields = MODULES[module]
    queryset = model.objects.none()
    if request.organization:
        queryset = _scope_to_user_branches(
            model.objects.filter(organization=request.organization), model, request
        ).order_by("-created_at")[:100]
    elif request.user.is_platform_admin or request.user.is_superuser:
        queryset = model.objects.all().order_by("-created_at")[:100]
    rows = [[getattr(item, field) for field in fields] for item in queryset]
    headers = [field.replace("_", " ").title() for field in fields]
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{module}.csv"'
        writer = csv.writer(response)
        writer.writerow(headers)
        writer.writerows(rows)
        return response
    return render(request, "module_overview.html", {"title": title, "headers": headers, "rows": rows})


@login_required
def global_search(request):
    query = request.GET.get("q", "").strip()
    results = []
    if query and (request.organization or request.user.is_platform_admin or request.user.is_superuser):
        organization_filter = {"organization": request.organization} if request.organization else {}
        branches = accessible_branches_for(request.user, request.organization) if request.organization else Branch.objects.all()
        searches = (
            ("Product", Product.objects.filter(**organization_filter).filter(Q(name__icontains=query) | Q(sku__icontains=query) | Q(barcode__icontains=query))),
            ("Serial / IMEI", StockUnit.objects.filter(**organization_filter, location__branch__in=branches).filter(Q(serial_number__icontains=query) | Q(secondary_serial__icontains=query))),
            ("Sale", Sale.objects.filter(**organization_filter, location__branch__in=branches, number__icontains=query)),
            ("Repair", RepairTicket.objects.filter(**organization_filter, branch__in=branches, number__icontains=query)),
        )
        for label, queryset in searches:
            results.extend((label, str(item)) for item in queryset[:10])
    return render(request, "search_results.html", {"query": query, "results": results})
