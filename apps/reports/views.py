import csv
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Count, DecimalField, F, IntegerField, OuterRef, Q, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.http import Http404, HttpResponse
from django.core.exceptions import PermissionDenied
from django.shortcuts import render
from django.urls import reverse
from django.utils.dateparse import parse_date
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.catalog.models import Brand, Category, Product
from apps.catalog.permissions import can_view_product_costs
from apps.contacts.models import Contact
from apps.commissions.models import CommissionAccrual, CommissionPayout
from apps.expenses.models import Expense
from apps.integrations.models import IntegrationEvent
from apps.inventory.models import SerialStatus, StockAdjustment, StockBalance, StockMovement, StockUnit
from apps.operations.models import ApprovalRequest, Payable, Receivable
from apps.organizations.models import AgentProfile, Branch, Location, LocationType, Membership, Organization, Role, SubscriptionInvoice
from apps.organizations.permissions import accessible_branches_for, organization_permission_required, user_has_organization_permission
from apps.payments.models import Payment, Refund
from apps.pos.models import POSSession
from apps.purchasing.models import PurchaseDiscrepancy, PurchaseOrder, PurchaseStatus, SupplierReturn
from apps.repairs.models import RepairTicket
from apps.sales.models import Sale, SaleReturn
from apps.transfers.models import StockTransfer, TransferStatus
from .exporting import build_simple_pdf, safe_csv_row


BRANCH_LOOKUPS = {
    Branch: "id",
    AgentProfile: "branch",
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

LANDING_FEATURE_CARDS = [
    "POS sales",
    "IMEI inventory",
    "Batch Excel intake",
    "Warehouse transfers",
    "Agent allocation",
    "Stock recall",
    "Credit sales",
    "Commissions",
    "Repairs and warranty",
    "Activity reports",
    "Role permissions",
    "Operational dashboards",
]

LANDING_ROADMAP_ITEMS = [
    "M-Pesa reconciliation",
    "eTIMS invoicing",
    "BI dashboards",
    "Stock forecasting",
    "Mobile apps",
    "Accounting APIs",
    "AI insights",
    "Barcode and QR",
    "Franchise control",
    "CRM loyalty",
]


PRODUCT_FEATURE_GROUPS = [
    {
        "title": "Business structure and access",
        "summary": "Give each company, branch, location and staff member a clear place in the business, without exposing another organization’s data.",
        "features": [
            {
                "name": "Multi-tenant business structure",
                "status": "complete",
                "what": "One platform can serve separate organizations, companies, branches and stock locations.",
                "how": "Every operational record is attached to an organization and, where relevant, a branch and location. Users only work inside the organization and branches assigned to them.",
                "scenario": "A retailer with Nairobi and Kisumu branches can review each branch separately while the owner sees the combined business picture.",
            },
            {
                "name": "Users, multiple roles and branch access",
                "status": "complete",
                "what": "A user can hold more than one role and receive access only to the branches they need.",
                "how": "Memberships link users to an organization, roles contribute permissions, and branch assignments limit operational data. Locked actions remain visible and open an access-request flow.",
                "scenario": "A branch manager can approve a transfer at their branch and also sell at the POS, without gaining access to another branch’s stock.",
            },
            {
                "name": "Grouped navigation and operational dashboards",
                "status": "complete",
                "what": "The working interface organizes stock, sales, people, money, repairs, approvals and reports into clear groups.",
                "how": "Navigation is role-aware while keeping unavailable actions visible. The dashboard changes its metrics and recent activity to the signed-in user’s permitted scope.",
                "scenario": "A cashier starts a sale quickly, while an owner opens the same system and sees sales, stock, approvals and activity summaries.",
            },
        ],
    },
    {
        "title": "Catalog, contacts and serialized inventory",
        "summary": "Create one reliable source for products, customers, suppliers and every tracked device.",
        "features": [
            {
                "name": "Products, brands and categories",
                "status": "complete",
                "what": "A product master holds the selling, cost, barcode, warranty, tax and tracking rules used across operations.",
                "how": "Products are created once, then selected in purchasing, intake, transfers, POS, repairs and reports. Products can be serialized for phones or quantity-based for accessories.",
                "scenario": "A Samsung phone is marked as serialized while a screen protector is sold by quantity, each following the correct workflow automatically.",
            },
            {
                "name": "Customers and suppliers",
                "status": "complete",
                "what": "Customer and supplier records provide the commercial identity behind sales, credit, purchasing and statements.",
                "how": "Contact profiles record type, contact details and active status; customer transactions feed receivables and supplier purchases feed payables and returns.",
                "scenario": "A repeat customer’s credit balance is visible before the cashier creates another credit sale, while purchasing can select the supplier for a new stock order.",
            },
            {
                "name": "IMEI and serial lifecycle tracking",
                "status": "complete",
                "what": "Each phone or tracked device has one central stock-unit record from receipt through transfer, allocation, sale, return or repair.",
                "how": "The system validates serial and IMEI values within the organization, prevents duplicates, stores current location and status, and provides IMEI history search.",
                "scenario": "An owner searches an IMEI and sees the device, its current or last location, movement history and related sale context instead of checking multiple spreadsheets.",
            },
            {
                "name": "Immutable stock movement ledger",
                "status": "complete",
                "what": "Completed stock movements are permanent business evidence rather than editable balances.",
                "how": "Purchases, transfers, allocations, sales, returns and adjustments post movement records. Completed entries cannot be edited or deleted; corrections are made as reversing movements.",
                "scenario": "When a transfer was posted to the wrong location, the manager records a reversal and the trail still shows both the original action and the correction.",
            },
            {
                "name": "Batch serial and IMEI intake",
                "status": "complete",
                "what": "Teams can add many serialized devices without creating one record at a time.",
                "how": "The intake screen accepts pasted serial or IMEI lines, CSV files and Excel workbooks. Each row is validated before stock is received, with valid items created and row-level issues reported.",
                "scenario": "A warehouse receives 120 phones, uploads the supplier spreadsheet, and immediately sees which IMEIs were accepted and which duplicates need attention.",
            },
            {
                "name": "Barcode and QR camera scanning",
                "status": "partial",
                "what": "Current forms are scanner-friendly and accept barcode, serial and IMEI text from hardware scanners.",
                "how": "A USB or Bluetooth scanner can type into focused identifier fields just like a keyboard. A dedicated in-browser phone-camera scanner is not yet built.",
                "scenario": "A warehouse operator can scan with a handheld scanner today; a field user cannot yet use the phone camera to scan a QR code directly inside MobiPOS.",
            },
        ],
    },
    {
        "title": "Purchasing, transfers and custody",
        "summary": "Move stock through the business with clear custody, confirmation and discrepancy control.",
        "features": [
            {
                "name": "Purchases, receiving and supplier returns",
                "status": "complete",
                "what": "Teams can order stock, approve an order, receive it, manage receiving differences and return items to suppliers.",
                "how": "Purchase lines retain ordered and received quantities. Receiving can flag a discrepancy, and supplier returns are linked back to the original purchase line.",
                "scenario": "A supplier delivers 48 phones against an order for 50. The receiver records 48, logs the shortfall and finance retains the correct payable context.",
            },
            {
                "name": "Warehouse, branch and agent transfers",
                "status": "complete",
                "what": "Existing stock can be searched and selected for controlled movement between locations, branches and agent custody.",
                "how": "Transfers use approval, dispatch and receipt stages. The receiving side confirms quantities, and discrepancies remain visible until resolved. Agent allocation and recall use the same controlled movement model.",
                "scenario": "A warehouse sends selected IMEIs to a branch, then allocates a subset to an agent. The owner can see the exact custodian at every stage.",
            },
            {
                "name": "Agent and DSA hierarchy",
                "status": "complete",
                "what": "Businesses can register agents and direct sales agents under their supervising agent when company policy allows it.",
                "how": "Agent profiles capture the hierarchy, verification state, documents and onboarding activity. Authorized users can approve, reject, allocate stock and recall stock.",
                "scenario": "A regional agent onboards a DSA, uploads ID evidence, waits for approval and then receives inventory only after the profile is approved.",
            },
            {
                "name": "National ID and onboarding documents",
                "status": "complete",
                "what": "Agent records can hold identification numbers, an image or document scan, photographs and supporting onboarding documents.",
                "how": "Documents are uploaded against the agent profile, and the audit trail records who created or changed the onboarding record. Direct camera capture is not a separate native scanning module.",
                "scenario": "A manager photographs an agent’s ID using the device camera upload control, attaches it to the profile and later downloads it during a compliance review.",
            },
        ],
    },
    {
        "title": "Sales, money and customer care",
        "summary": "Sell serialized and quantity stock, accept the payment mix customers use, and retain control after the sale.",
        "features": [
            {
                "name": "POS cart, checkout and payment methods",
                "status": "complete",
                "what": "The POS supports cart building, serialized items, accessories, receipts and recorded cash, M-Pesa, card, bank and credit payments.",
                "how": "Cashiers open a session, add product lines, complete the cart and record one or more payments. Split payment totals are tracked against the sale and the session can be reviewed and closed.",
                "scenario": "A customer pays KES 10,000 by M-Pesa and KES 2,500 in cash. The cashier records both methods against one phone sale and the session totals reconcile later.",
            },
            {
                "name": "Customer credit, receivables and installments",
                "status": "complete",
                "what": "Credit sales can be controlled by customer limits, then followed as outstanding receivables with scheduled installments.",
                "how": "A credit sale creates a receivable. Staff can record and schedule installments, while operational reports surface aging and outstanding balances.",
                "scenario": "A trusted customer takes a phone on credit. The business records the limit, schedules three monthly payments and checks overdue balances before approving new credit.",
            },
            {
                "name": "Returns, refunds, warranties and repairs",
                "status": "complete",
                "what": "The business can request and approve a line-level return, record refunds, track warranty cases and manage repair tickets with parts usage.",
                "how": "Returns remain tied to the original sale line. Repair tickets hold customer/device context and can record parts taken from stock, maintaining a traceable after-sales path.",
                "scenario": "A customer returns a faulty phone. Staff locate the sale, request the return, route it to warranty or repair, and retain the financial and stock history.",
            },
            {
                "name": "Live M-Pesa confirmation and reconciliation",
                "status": "partial",
                "what": "M-Pesa can be recorded as a payment method today, but live Daraja callbacks and automated confirmation are not connected.",
                "how": "The integration outbox and adapter foundation exist, but live credentials, callback handling, reconciliation and production monitoring must be implemented before M-Pesa is treated as automatically confirmed.",
                "scenario": "A cashier can record an M-Pesa reference now; the future live connection will validate the payment and reconcile it without manual checking.",
            },
        ],
    },
    {
        "title": "Control, reporting and scale",
        "summary": "Give owners a reliable view of actions, exceptions and the areas that require attention.",
        "features": [
            {
                "name": "Expenses, commissions and approvals",
                "status": "complete",
                "what": "Expenses, commission accruals and payout workflows have approval and payment controls.",
                "how": "The approval inbox handles configured approvals, while commission records connect to sales and payout status. Expenses and operational money movements appear in reports.",
                "scenario": "A salesperson earns commission from a paid sale, a manager approves the payout, and the owner can later see the approval and payment history.",
            },
            {
                "name": "Owner activity reporting and audit trail",
                "status": "complete",
                "what": "Authorized owners and platform administrators can review platform or organization activity, including actor summaries and login activity.",
                "how": "Audit events capture login, stock, user, transfer, sale, payment, approval, repair and integration events when they occur. The activity report filters and summarizes the recorded history.",
                "scenario": "An owner filters activity for Renny and sees login events, stock changes and access-related actions with time and organizational context.",
            },
            {
                "name": "Operational, retail, exception, IMEI and agent reports",
                "status": "complete",
                "what": "Reports cover operational totals, sales and retail signals, aging and exceptions, device history and agent-network performance.",
                "how": "Each report scopes data to the active organization and permitted branches, so managers see relevant data while owners maintain broader visibility.",
                "scenario": "Before a weekly meeting, the owner reviews aging stock, outstanding credit, pending receipts, agent stock and the IMEI history of an escalated device.",
            },
            {
                "name": "eTIMS, advanced BI, forecasting, loyalty and mobile apps",
                "status": "planned",
                "what": "These are important growth capabilities, but they are not production-complete features in the current release.",
                "how": "The platform has a foundation for integration events and background work, but live eTIMS invoicing, forecasting, CRM loyalty, native mobile apps and AI recommendations need dedicated delivery and validation.",
                "scenario": "A future owner dashboard will recommend purchase quantities using demand trends; today the team uses operational and retail reports to make that decision.",
            },
        ],
    },
]


def product_features(request):
    return render(request, "marketing/features.html", {"feature_groups": PRODUCT_FEATURE_GROUPS})


def _scope_to_user_branches(queryset, model, request):
    branches = accessible_branches_for(request.user, request.organization)
    if model is StockTransfer:
        return queryset.filter(Q(source__branch__in=branches) | Q(destination__branch__in=branches)).distinct()
    lookup = BRANCH_LOOKUPS.get(model)
    return queryset.filter(**{f"{lookup}__in": branches}) if lookup else queryset


def _can_view_module(request, module):
    if module in SENSITIVE_MODULES and request.organization:
        membership = request.membership
        if request.user.is_superuser or request.user.is_platform_admin or (membership and membership.is_owner):
            return True
        return False
    permission = MODULE_PERMISSIONS.get(module)
    if permission and request.organization:
        from apps.organizations.permissions import user_has_organization_permission
        return user_has_organization_permission(request.user, request.organization, permission)
    return True


def _owner_activity_queryset(request):
    if request.user.is_superuser or request.user.is_platform_admin:
        return AuditEvent.objects.all()

    organization = getattr(request, "organization", None)
    membership = getattr(request, "membership", None)
    if not organization or not membership:
        raise PermissionDenied("Organization owner access is required.")
    if not membership.is_owner and not user_has_organization_permission(
        request.user, organization, "organizations.view_activity_report"
    ):
        raise PermissionDenied("Organization owner access is required.")

    member_user_ids = Membership.objects.filter(
        organization=organization,
        status="active",
    ).values("user_id")
    return AuditEvent.objects.filter(
        Q(organization=organization) | Q(organization__isnull=True, actor_id__in=member_user_ids)
    )


def dashboard(request):
    if not request.user.is_authenticated:
        return render(request, "marketing/landing.html", {
            "feature_cards": LANDING_FEATURE_CARDS,
            "roadmap_items": LANDING_ROADMAP_ITEMS,
        })
    organization = request.organization
    latest_activity = []
    if organization:
        branches = accessible_branches_for(request.user, organization)
        today = timezone.localdate()
        available_stock = StockUnit.objects.filter(
            organization=organization,
            location__branch__in=branches,
            status=SerialStatus.AVAILABLE,
        )
        sales = Sale.objects.filter(organization=organization, location__branch__in=branches)
        metrics = {
            "organizations": 1,
            "branches": branches.count(),
            "active_users": Membership.objects.filter(organization=organization, status="active").count(),
            "pending_approvals": ApprovalRequest.objects.filter(organization=organization, status="pending").count(),
            "sales_total": sales.aggregate(total=Sum("total"))["total"] or 0,
            "todays_sales_total": sales.filter(completed_at__date=today).aggregate(total=Sum("total"))["total"] or 0,
            "todays_sales_count": sales.filter(completed_at__date=today).count(),
            "todays_purchases_total": PurchaseOrder.objects.filter(
                organization=organization,
                destination__branch__in=branches,
                created_at__date=today,
            ).aggregate(total=Sum(F("lines__quantity") * F("lines__unit_cost")))["total"] or 0,
            "pending_purchase_receipts": PurchaseOrder.objects.filter(
                organization=organization,
                destination__branch__in=branches,
                status__in=(PurchaseStatus.APPROVED, PurchaseStatus.PART_RECEIVED, PurchaseStatus.DISCREPANCY),
            ).count(),
            "pending_transfer_receipts": StockTransfer.objects.filter(
                organization=organization,
                destination__branch__in=branches,
                status=TransferStatus.IN_TRANSIT,
            ).count(),
            "active_carts": sales.filter(status="draft").count(),
            "aged_stock": available_stock.filter(created_at__lte=timezone.now() - timedelta(days=5)).count(),
            "unpaid_commissions": CommissionAccrual.objects.filter(
                organization=organization,
                sale__location__branch__in=branches,
                is_payable=True,
                paid_at__isnull=True,
            ).aggregate(total=Sum("amount"))["total"] or 0,
            "stock_units": available_stock.count(),
            "expenses_total": Expense.objects.filter(organization=organization, branch__in=branches).aggregate(total=Sum("amount"))["total"] or 0,
            "integration_failures": IntegrationEvent.objects.filter(organization=organization, status="failed").count(),
        }
        latest_sales = sales.select_related("customer", "agent").prefetch_related("lines__product", "lines__stock_unit").order_by("-created_at")[:8]
        pending_actions = {
            "purchase_receipts": PurchaseOrder.objects.filter(
                organization=organization,
                destination__branch__in=branches,
                status__in=(PurchaseStatus.APPROVED, PurchaseStatus.PART_RECEIVED, PurchaseStatus.DISCREPANCY),
            ).select_related("supplier", "destination")[:5],
            "transfer_receipts": StockTransfer.objects.filter(
                organization=organization,
                destination__branch__in=branches,
                status=TransferStatus.IN_TRANSIT,
            ).select_related("source", "destination")[:5],
        }
        membership = getattr(request, "membership", None)
        if request.user.is_superuser or request.user.is_platform_admin or (membership and membership.is_owner):
            latest_activity = _owner_activity_queryset(request).select_related("actor", "organization")[:8]
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
            "todays_sales_total": 0,
            "todays_sales_count": 0,
            "todays_purchases_total": 0,
            "pending_purchase_receipts": 0,
            "pending_transfer_receipts": 0,
            "active_carts": 0,
            "aged_stock": 0,
            "unpaid_commissions": 0,
        }
        latest_sales = Sale.objects.select_related("customer", "agent").prefetch_related("lines__product", "lines__stock_unit").order_by("-created_at")[:8]
        pending_actions = {"purchase_receipts": [], "transfer_receipts": []}
        latest_activity = _owner_activity_queryset(request).select_related("actor", "organization")[:8]
    else:
        metrics = dict.fromkeys(
            (
                "organizations", "branches", "active_users", "pending_approvals",
                "sales_total", "todays_sales_total", "todays_sales_count",
                "todays_purchases_total", "pending_purchase_receipts",
                "pending_transfer_receipts", "active_carts", "aged_stock",
                "unpaid_commissions", "stock_units", "expenses_total",
                "integration_failures",
            ),
            0,
        )
        latest_sales = []
        pending_actions = {"purchase_receipts": [], "transfer_receipts": []}
    return render(request, "dashboard.html", {
        "metrics": metrics,
        "latest_sales": latest_sales,
        "pending_actions": pending_actions,
        "latest_activity": latest_activity,
    })


def demo_access(request):
    if request.user.is_authenticated:
        return render(request, "marketing/demo.html", {"already_signed_in": True})
    demo_accounts = [
        {
            "username": "brian",
            "password": "DemoPass123!",
            "role": "Owner demo",
            "organization": "Nairobi Mobile Hub",
            "best_for": "Full business owner view with Kenyan mobile retail demo data.",
        },
        {
            "username": "alice",
            "password": "DemoPass123!",
            "role": "Owner demo",
            "organization": "MobiPOS Electronics",
            "best_for": "Testing dashboards, inventory, sales, transfers, users, and reports.",
        },
        {
            "username": "platformadmin",
            "password": "AdminPass123!",
            "role": "Platform administrator",
            "organization": "Platform-wide",
            "best_for": "Platform administration and Django admin access.",
        },
    ]
    return render(request, "marketing/demo.html", {"demo_accounts": demo_accounts})


@login_required
def activity_report(request):
    queryset = _owner_activity_queryset(request).select_related("actor", "organization")

    query = request.GET.get("q", "").strip()
    action = request.GET.get("action", "").strip()
    actor = request.GET.get("actor", "").strip()
    start = parse_date(request.GET.get("start", ""))
    end = parse_date(request.GET.get("end", ""))

    if query:
        queryset = queryset.filter(
            Q(action__icontains=query)
            | Q(message__icontains=query)
            | Q(target_type__icontains=query)
            | Q(target_id__icontains=query)
            | Q(actor__username__icontains=query)
            | Q(actor__email__icontains=query)
            | Q(actor__first_name__icontains=query)
            | Q(actor__last_name__icontains=query)
        )
    if action:
        queryset = queryset.filter(action=action)
    if actor:
        queryset = queryset.filter(actor_id=actor)
    if start:
        queryset = queryset.filter(created_at__date__gte=start)
    if end:
        queryset = queryset.filter(created_at__date__lte=end)

    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="activity-report.csv"'
        writer = csv.writer(response)
        writer.writerow(["Time", "Actor", "Organization", "Action", "Target", "Message", "IP address"])
        for event in queryset.order_by("-created_at"):
            writer.writerow(safe_csv_row([
                event.created_at.isoformat(),
                event.actor.get_username() if event.actor else "System",
                event.organization.name if event.organization else "Platform",
                event.action,
                f"{event.target_type} {event.target_id}".strip(),
                event.message,
                event.ip_address or "",
            ]))
        return response

    scoped = queryset.order_by("-created_at")
    page_obj = Paginator(scoped, 25).get_page(request.GET.get("page"))
    available_actions = (
        _owner_activity_queryset(request)
        .exclude(action="")
        .values_list("action", flat=True)
        .distinct()
        .order_by("action")
    )
    available_actors = User.objects.filter(
        id__in=_owner_activity_queryset(request).exclude(actor__isnull=True).values("actor_id")
    ).order_by("first_name", "last_name", "username")
    actor_summary = (
        queryset.exclude(actor__isnull=True)
        .values("actor_id", "actor__first_name", "actor__last_name", "actor__username", "actor__email")
        .annotate(total=Count("id"), logins=Count("id", filter=Q(action="auth.login")))
        .order_by("-total")[:10]
    )
    action_summary = (
        queryset.values("action")
        .annotate(total=Count("id"))
        .order_by("-total", "action")[:12]
    )

    return render(request, "reports/activity.html", {
        "page_obj": page_obj,
        "events": page_obj.object_list,
        "query": query,
        "selected_action": action,
        "selected_actor": actor,
        "start": start,
        "end": end,
        "available_actions": available_actions,
        "available_actors": available_actors,
        "actor_summary": actor_summary,
        "action_summary": action_summary,
        "total_events": queryset.count(),
    })


MODULES = {
    "products": ("Products", Product, ("name", "sku", "selling_price", "is_serialized")),
    "contacts": ("Customers and suppliers", Contact, ("name", "contact_type", "phone_number", "credit_limit", "is_active")),
    "inventory": ("Serialized inventory", StockUnit, ("serial_number", "product", "status", "location")),
    "agent-stock": ("Agent stock custody", StockUnit, ("serial_number", "secondary_serial", "product", "status", "location")),
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
    "agents": ("Agent profiles", AgentProfile, ("legal_name", "profile_type", "status", "branch", "supervisor")),
    "branches": ("Branches", Branch, ("name", "code", "company", "is_active")),
    "locations": ("Locations", Location, ("name", "code", "branch", "location_type", "is_active")),
    "roles": ("Roles", Role, ("name", "code", "description", "is_active")),
    "aged-stock": ("Aged stock", StockUnit, ("serial_number", "product", "location", "status", "created_at")),
}
SENSITIVE_MODULES = {
    "approvals", "branches", "commission-payouts", "integrations", "locations",
    "roles", "subscriptions", "users", "agents",
}
MODULE_PERMISSIONS = {
    "products": "catalog.view_product",
    "contacts": "contacts.view_contact",
    "inventory": "inventory.view_stockunit",
    "agent-stock": "inventory.view_stockunit",
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
    "aged-stock": "inventory.view_stockunit",
}
MODULE_DETAIL_URLS = {
    "products": "product-detail",
    "contacts": "contact-detail",
    "adjustments": "stock-adjustment-detail",
    "purchases": "purchase-detail",
    "supplier-returns": "supplier-return-detail",
    "transfers": "transfer-detail",
    "sales": "sale-detail",
    "commission-payouts": "commission-payout-detail",
    "repairs": "repair-detail",
    "sessions": "session-detail",
    "agents": "agent-profile-detail",
}


def _product_register(request, title):
    organization = request.organization
    can_view_costs = can_view_product_costs(request.user, organization)
    branches = accessible_branches_for(request.user, organization) if organization else Branch.objects.all()
    queryset = Product.objects.none()
    if organization:
        queryset = Product.objects.filter(organization=organization)
    elif request.user.is_platform_admin or request.user.is_superuser:
        queryset = Product.objects.all()

    balance_subquery = StockBalance.objects.filter(
        product=OuterRef("pk"),
        location__branch__in=branches,
    )
    available_unit_subquery = StockUnit.objects.filter(
        product=OuterRef("pk"),
        location__branch__in=branches,
        status=SerialStatus.AVAILABLE,
    )
    sold_unit_subquery = StockUnit.objects.filter(
        product=OuterRef("pk"),
        status=SerialStatus.SOLD,
    ).filter(
        Q(location__branch__in=branches)
        | Q(saleline__sale__location__branch__in=branches)
        | Q(movements__location__branch__in=branches)
    )
    if organization:
        balance_subquery = balance_subquery.filter(organization=organization)
        available_unit_subquery = available_unit_subquery.filter(organization=organization)
        sold_unit_subquery = sold_unit_subquery.filter(organization=organization)

    queryset = queryset.select_related("category", "brand").annotate(
        scoped_stock_total=Coalesce(
            Subquery(
                balance_subquery.values("product").annotate(total=Sum("quantity")).values("total")[:1],
                output_field=DecimalField(max_digits=14, decimal_places=3),
            ),
            Value(0),
            output_field=DecimalField(max_digits=14, decimal_places=3),
        ),
        available_serial_count=Coalesce(
            Subquery(
                available_unit_subquery.values("product").annotate(total=Count("id")).values("total")[:1],
                output_field=IntegerField(),
            ),
            Value(0),
            output_field=IntegerField(),
        ),
        sold_serial_count=Coalesce(
            Subquery(
                sold_unit_subquery.values("product").annotate(total=Count("id", distinct=True)).values("total")[:1],
                output_field=IntegerField(),
            ),
            Value(0),
            output_field=IntegerField(),
        ),
    )

    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    tracking = request.GET.get("tracking", "").strip()
    stock_status = request.GET.get("stock_status", "").strip()
    category_id = request.GET.get("category", "").strip()
    brand_id = request.GET.get("brand", "").strip()

    if query:
        queryset = queryset.filter(
            Q(name__icontains=query)
            | Q(sku__icontains=query)
            | Q(barcode__icontains=query)
            | Q(stock_units__serial_number__icontains=query)
            | Q(stock_units__secondary_serial__icontains=query)
        ).distinct()
    if status in {"active", "inactive"}:
        queryset = queryset.filter(is_active=status == "active")
    if tracking == "serialized":
        queryset = queryset.filter(is_serialized=True)
    elif tracking == "quantity":
        queryset = queryset.filter(is_serialized=False)
    if category_id:
        queryset = queryset.filter(category_id=category_id)
    if brand_id:
        queryset = queryset.filter(brand_id=brand_id)
    if stock_status == "in_stock":
        queryset = queryset.filter(scoped_stock_total__gt=0)
    elif stock_status == "out_of_stock":
        queryset = queryset.filter(scoped_stock_total__lte=0)
    elif stock_status == "low_stock":
        queryset = queryset.filter(is_stocked=True, scoped_stock_total__gt=0, scoped_stock_total__lte=F("reorder_level"))

    queryset = queryset.order_by("name", "sku")

    export_headers = [
        "Product", "SKU", "Barcode", "Category", "Brand", "Tracking",
        "Current stock", "Available IMEIs", "Sold IMEIs", "Selling price", "Active",
    ]
    if can_view_costs:
        export_headers.extend(["Cost price", "Margin amount", "Margin percent"])

    def product_export_rows():
        for product in queryset:
            row = [
                product.name,
                product.sku,
                product.barcode,
                product.category.name if product.category else "",
                product.brand.name if product.brand else "",
                "Serialized / IMEI" if product.is_serialized else "Quantity",
                product.scoped_stock_total,
                product.available_serial_count,
                product.sold_serial_count,
                product.selling_price,
                "Yes" if product.is_active else "No",
            ]
            if can_view_costs:
                margin_amount = product.selling_price - product.cost_price
                margin_percent = (margin_amount / product.selling_price * 100) if product.selling_price else 0
                row.extend([product.cost_price, margin_amount, f"{margin_percent:.2f}"])
            yield row

    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = 'attachment; filename="products.csv"'
        writer = csv.writer(response)
        writer.writerow(export_headers)
        for row in product_export_rows():
            writer.writerow(safe_csv_row(row))
        return response
    if request.GET.get("format") == "pdf":
        response = HttpResponse(
            build_simple_pdf(title="MobiPOS Products", headers=export_headers, rows=list(product_export_rows())),
            content_type="application/pdf",
        )
        response["Content-Disposition"] = 'attachment; filename="products.pdf"'
        return response

    page_obj = Paginator(queryset, 25).get_page(request.GET.get("page"))
    page_products = list(page_obj.object_list)
    product_ids = [product.id for product in page_products]
    location_summaries = {}
    if product_ids:
        for balance in StockBalance.objects.filter(
            organization=organization,
            product_id__in=product_ids,
            location__branch__in=branches,
        ).select_related("location", "location__branch").order_by("location__branch__name", "location__name"):
            location_summaries.setdefault(balance.product_id, []).append(balance)

    rows = []
    for product in page_products:
        margin_amount = product.selling_price - product.cost_price
        margin_percent = (margin_amount / product.selling_price * 100) if product.selling_price else 0
        rows.append({
            "product": product,
            "detail_url": reverse("product-detail", args=[product.pk]),
            "stock_total": product.scoped_stock_total,
            "available_serial_count": product.available_serial_count,
            "sold_serial_count": product.sold_serial_count,
            "balances": location_summaries.get(product.id, [])[:4],
            "balance_count": len(location_summaries.get(product.id, [])),
            "is_low_stock": product.is_stocked and product.scoped_stock_total > 0 and product.scoped_stock_total <= product.reorder_level,
            "is_out_of_stock": product.is_stocked and product.scoped_stock_total <= 0,
            "margin_amount": margin_amount,
            "margin_percent": margin_percent,
            "values": [product.name, product.sku, product.selling_price, product.is_serialized],
        })

    base_queryset = Product.objects.filter(organization=organization) if organization else Product.objects.all()
    category_options = Category.objects.filter(organization=organization, is_active=True).order_by("name") if organization else Category.objects.none()
    brand_options = Brand.objects.filter(organization=organization, is_active=True).order_by("name") if organization else Brand.objects.none()
    balance_filters = {"product__in": queryset, "location__branch__in": branches}
    unit_filters = {"product__in": queryset, "location__branch__in": branches}
    if organization:
        balance_filters["organization"] = organization
        unit_filters["organization"] = organization
    metrics = {
        "products": queryset.count(),
        "available_stock": StockBalance.objects.filter(**balance_filters).aggregate(total=Sum("quantity"))["total"] or 0,
        "available_serials": StockUnit.objects.filter(**unit_filters, status=SerialStatus.AVAILABLE).count(),
        "low_stock": queryset.filter(is_stocked=True, scoped_stock_total__gt=0, scoped_stock_total__lte=F("reorder_level")).count(),
        "all_products": base_queryset.count(),
    }

    return render(request, "catalog/register.html", {
        "title": title,
        "rows": rows,
        "page_obj": page_obj,
        "query": query,
        "status": status,
        "tracking": tracking,
        "stock_status": stock_status,
        "selected_category": category_id,
        "selected_brand": brand_id,
        "category_options": category_options,
        "brand_options": brand_options,
        "metrics": metrics,
        "can_view_costs": can_view_costs,
        "can_add_product": user_has_organization_permission(request.user, organization, "catalog.add_product") if organization else request.user.is_superuser,
        "supports_status_filter": True,
        "has_detail_pages": True,
    })


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
    if module == "products":
        return _product_register(request, title)
    queryset = model.objects.none()
    if request.organization:
        queryset = _scope_to_user_branches(
            model.objects.filter(organization=request.organization), model, request
        ).order_by("-created_at")
    elif request.user.is_platform_admin or request.user.is_superuser:
        queryset = model.objects.all().order_by("-created_at")
    relation_field_names = {
        model_field.name for model_field in model._meta.fields if model_field.is_relation
    }
    related_fields = [field for field in fields if field in relation_field_names]
    if related_fields:
        queryset = queryset.select_related(*related_fields)
    query = request.GET.get("q", "").strip()
    if query:
        searchable_fields = [
            field.name for field in model._meta.fields
            if field.name in fields and field.get_internal_type() in {"CharField", "TextField", "EmailField", "SlugField"}
        ]
        search_filter = Q()
        for field in searchable_fields:
            search_filter |= Q(**{f"{field}__icontains": query})
        if searchable_fields:
            queryset = queryset.filter(search_filter)
    status = request.GET.get("status", "").strip()
    if status in {"active", "inactive"} and any(field.name == "is_active" for field in model._meta.fields):
        queryset = queryset.filter(is_active=status == "active")
    if module == "aged-stock":
        queryset = queryset.filter(status=SerialStatus.AVAILABLE, created_at__lte=timezone.now() - timedelta(days=5))
    if module == "agent-stock":
        queryset = queryset.filter(location__location_type=LocationType.AGENT)
    headers = [field.replace("_", " ").title() for field in fields]
    if request.GET.get("format") == "csv":
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{module}.csv"'
        writer = csv.writer(response)
        writer.writerow(headers)
        writer.writerows(safe_csv_row(getattr(item, field) for field in fields) for item in queryset)
        return response
    if request.GET.get("format") == "pdf":
        rows_for_pdf = ([getattr(item, field) for field in fields] for item in queryset[:80])
        response = HttpResponse(
            build_simple_pdf(title=f"MobiPOS {title}", headers=headers, rows=list(rows_for_pdf)),
            content_type="application/pdf",
        )
        response["Content-Disposition"] = f'attachment; filename="{module}.pdf"'
        return response
    page_obj = Paginator(queryset, 25).get_page(request.GET.get("page"))
    detail_url_name = MODULE_DETAIL_URLS.get(module)
    rows = [{
        "values": [getattr(item, field) for field in fields],
        "detail_url": reverse(detail_url_name, args=[item.pk]) if detail_url_name else "",
    } for item in page_obj]
    return render(
        request,
        "module_overview.html",
        {
            "title": title,
            "headers": headers,
            "rows": rows,
            "page_obj": page_obj,
            "query": query,
            "status": status,
            "supports_status_filter": any(field.name == "is_active" for field in model._meta.fields),
            "has_detail_pages": bool(detail_url_name),
        },
    )


@login_required
def global_search(request):
    query = request.GET.get("q", "").strip()
    results = []
    if query and (request.organization or request.user.is_platform_admin or request.user.is_superuser):
        organization_filter = {"organization": request.organization} if request.organization else {}
        branches = accessible_branches_for(request.user, request.organization) if request.organization else Branch.objects.all()
        searches = []
        if _can_view_module(request, "products"):
            searches.append(("Product", Product.objects.filter(**organization_filter).filter(Q(name__icontains=query) | Q(sku__icontains=query) | Q(barcode__icontains=query)), "product-detail"))
        if _can_view_module(request, "contacts"):
            searches.append(("Contact", Contact.objects.filter(**organization_filter).filter(Q(name__icontains=query) | Q(phone_number__icontains=query) | Q(email__icontains=query) | Q(tax_number__icontains=query)), "contact-detail"))
        if _can_view_module(request, "inventory"):
            searches.append(("Serial / IMEI", StockUnit.objects.filter(**organization_filter).filter(
                Q(location__branch__in=branches)
                | Q(saleline__sale__location__branch__in=branches)
                | Q(movements__location__branch__in=branches)
            ).filter(Q(serial_number__icontains=query) | Q(secondary_serial__icontains=query)).distinct(), "imei-history"))
        if _can_view_module(request, "sales"):
            searches.append(("Sale", Sale.objects.filter(**organization_filter, location__branch__in=branches, number__icontains=query), "sale-detail"))
        if _can_view_module(request, "repairs"):
            searches.append(("Repair", RepairTicket.objects.filter(**organization_filter, branch__in=branches, number__icontains=query), "repair-detail"))
        for label, queryset, url_name in searches:
            results.extend({
                "type": label,
                "label": str(item),
                "url": reverse(url_name, args=[item.pk]) if url_name and url_name != "imei-history" else (
                    f"{reverse('imei-history')}?q={item.serial_number}" if url_name == "imei-history" else ""
                ),
            } for item in queryset[:10])
    return render(request, "search_results.html", {"query": query, "results": results})


@login_required
@organization_permission_required("inventory.view_stockunit")
def imei_history(request):
    query = request.GET.get("q", "").strip()
    status = request.GET.get("status", "").strip()
    branches = accessible_branches_for(request.user, request.organization)
    units = StockUnit.objects.filter(
        organization=request.organization,
    ).filter(
        Q(location__branch__in=branches)
        | Q(saleline__sale__location__branch__in=branches)
        | Q(movements__location__branch__in=branches)
    ).select_related("product", "location", "location__branch").distinct()
    if query:
        units = units.filter(
            Q(serial_number__icontains=query)
            | Q(secondary_serial__icontains=query)
            | Q(product__name__icontains=query)
            | Q(product__sku__icontains=query)
        )
    valid_statuses = {choice.value for choice in SerialStatus}
    if status in valid_statuses:
        units = units.filter(status=status)
    units = units.order_by("product__name", "serial_number")
    unit = None
    movements = StockMovement.objects.none()
    sales = Sale.objects.none()
    purchases = PurchaseOrder.objects.none()
    if query and request.organization:
        unit = units.first()
        if unit:
            movements = StockMovement.objects.filter(
                organization=request.organization,
                stock_unit=unit,
            ).select_related("product", "location", "actor")[:50]
            sales = Sale.objects.filter(
                organization=request.organization,
                lines__stock_unit=unit,
                location__branch__in=branches,
            ).select_related("customer", "agent").distinct()
            purchases = PurchaseOrder.objects.filter(
                organization=request.organization,
                lines__product=unit.product,
                destination__branch__in=branches,
            ).select_related("supplier", "destination").distinct()[:10]
    page_obj = Paginator(units, 25).get_page(request.GET.get("page"))
    scoped_units = StockUnit.objects.filter(
        organization=request.organization,
    ).filter(
        Q(location__branch__in=branches)
        | Q(saleline__sale__location__branch__in=branches)
        | Q(movements__location__branch__in=branches)
    ).distinct()
    return render(request, "reports/imei_history.html", {
        "query": query,
        "status": status,
        "status_options": SerialStatus.choices,
        "units": page_obj.object_list,
        "page_obj": page_obj,
        "unit": unit,
        "movements": movements,
        "sales": sales,
        "purchases": purchases,
        "metrics": {
            "total": scoped_units.count(),
            "available": scoped_units.filter(status=SerialStatus.AVAILABLE).count(),
            "sold": scoped_units.filter(status=SerialStatus.SOLD).count(),
            "in_transfer": scoped_units.filter(status=SerialStatus.IN_TRANSFER).count(),
            "exceptions": scoped_units.filter(
                status__in=(SerialStatus.DAMAGED, SerialStatus.WARRANTY_REPAIR, SerialStatus.WRITTEN_OFF)
            ).count(),
        },
    })
