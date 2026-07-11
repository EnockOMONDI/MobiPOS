from django.urls import reverse

from .permissions import user_has_organization_permission


ICONS = {
    "home": "M3 10.5 12 3l9 7.5V21a.75.75 0 0 1-.75.75H15v-6h-6v6H3.75A.75.75 0 0 1 3 21V10.5Z",
    "sparkles": "m9.813 3.146.438 1.314a2.25 2.25 0 0 0 1.423 1.423l1.314.438-1.314.438a2.25 2.25 0 0 0-1.423 1.423l-.438 1.314-.438-1.314a2.25 2.25 0 0 0-1.423-1.423l-1.314-.438 1.314-.438A2.25 2.25 0 0 0 9.375 4.46l.438-1.314ZM18.75 9.75l.657 1.971a2.25 2.25 0 0 0 1.423 1.423l1.971.657-1.971.657a2.25 2.25 0 0 0-1.423 1.423l-.657 1.971-.657-1.971a2.25 2.25 0 0 0-1.423-1.423l-1.971-.657 1.971-.657a2.25 2.25 0 0 0 1.423-1.423l.657-1.971ZM6.75 14.25l.526 1.578a2.25 2.25 0 0 0 1.423 1.423l1.578.526-1.578.526a2.25 2.25 0 0 0-1.423 1.423l-.526 1.578-.526-1.578a2.25 2.25 0 0 0-1.423-1.423l-1.578-.526 1.578-.526a2.25 2.25 0 0 0 1.423-1.423l.526-1.578Z",
    "cart": "M2.25 3h1.386a1.5 1.5 0 0 1 1.455 1.136L5.4 5.37h14.85l-1.5 7.5H7.275m0 0-.525 2.625h10.5m-9.75 3.75h.008v.008H7.5v-.008Zm9 0h.008v.008H16.5v-.008Z",
    "box": "m21 8.25-9-5.25-9 5.25m18 0-9 5.25m9-5.25v7.5L12 21m0-7.5L3 8.25m9 5.25V21M3 8.25v7.5L12 21",
    "truck": "M8.25 18.75a1.5 1.5 0 1 1-3 0m3 0a1.5 1.5 0 0 0-3 0m3 0h7.5m-10.5 0H3.75V6.75h11.25v12m.75 0a1.5 1.5 0 1 1 3 0m-3 0a1.5 1.5 0 0 0 3 0m0 0h1.5v-6l-3-3h-2.25v9",
    "wallet": "M21 12.75V8.25A2.25 2.25 0 0 0 18.75 6h-13.5A2.25 2.25 0 0 0 3 8.25v9A2.25 2.25 0 0 0 5.25 19.5h13.5A2.25 2.25 0 0 0 21 17.25v-4.5Zm0 0h-4.5a2.25 2.25 0 0 1 0-4.5H21",
    "wrench": "M11.42 15.17 17.25 21a2.121 2.121 0 0 0 3-3l-5.83-5.83M11.42 15.17l2.83-2.83m-2.83 2.83-4.59 4.59a2.121 2.121 0 0 1-3-3l4.59-4.59m5.83.17a6 6 0 0 0-7.59-7.59l3.67 3.67-2.91 2.91-3.67-3.67a6 6 0 0 0 7.59 7.59Z",
    "chart": "M3 13.5h4.5V21H3v-7.5Zm6.75-6h4.5V21h-4.5V7.5Zm6.75-4.5H21v18h-4.5V3Z",
    "cog": "M9.594 3.94c.09-.542.56-.94 1.11-.94h2.592c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.325.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 0 1 1.37.49l1.296 2.244c.275.476.17 1.079-.247 1.438l-.982.849c-.287.247-.432.614-.425.993a7.5 7.5 0 0 1 0 .257c-.007.378.138.745.425.993l.982.848c.417.36.522.962.247 1.438l-1.296 2.245a1.125 1.125 0 0 1-1.37.489l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.5 6.5 0 0 1-.22.128c-.331.183-.581.495-.644.869l-.213 1.281c-.09.543-.56.94-1.11.94h-2.592c-.55 0-1.02-.397-1.11-.94l-.213-1.281c-.063-.374-.313-.686-.645-.87a6.52 6.52 0 0 1-.22-.127c-.325-.196-.72-.257-1.075-.124l-1.217.456a1.125 1.125 0 0 1-1.37-.49L3.58 15.5a1.125 1.125 0 0 1 .247-1.438l.982-.848c.287-.248.432-.615.425-.993a7.5 7.5 0 0 1 0-.257c.007-.379-.138-.746-.425-.993l-.982-.85A1.125 1.125 0 0 1 3.58 8.684L4.876 6.44a1.125 1.125 0 0 1 1.37-.49l1.217.456c.355.133.75.072 1.076-.124.072-.044.145-.086.219-.127.332-.184.582-.496.645-.87l.213-1.281ZM12 15.75a3.75 3.75 0 1 0 0-7.5 3.75 3.75 0 0 0 0 7.5Z",
    "bell": "M14.857 17.082a23.848 23.848 0 0 0 5.454-1.31A8.967 8.967 0 0 1 18 9.75V9A6 6 0 0 0 6 9v.75a8.967 8.967 0 0 1-2.312 6.022 23.848 23.848 0 0 0 5.455 1.31m5.714 0a24.255 24.255 0 0 1-5.714 0m5.714 0a3 3 0 1 1-5.714 0",
    "lock": "M16.5 10.5V6.75a4.5 4.5 0 0 0-9 0v3.75m-.75 0h10.5A2.25 2.25 0 0 1 19.5 12.75v7.5H4.5v-7.5a2.25 2.25 0 0 1 2.25-2.25Z",
}


def item(label, url_name, icon, permission=None, args=(), owner_only=False, requestable=True, staff_only=False):
    return {
        "label": label,
        "url_name": url_name,
        "args": args,
        "icon_path": ICONS[icon],
        "permission": permission,
        "owner_only": owner_only,
        "requestable": requestable,
        "staff_only": staff_only,
    }


NAVIGATION_GROUPS = (
    ("Overview", "sparkles", (
        item("Dashboard", "dashboard", "home"),
        item("Approval inbox", "approval-inbox", "lock"),
        item("Notifications", "notification-list", "bell"),
    )),
    ("Sales & POS", "cart", (
        item("Open register", "session-open", "wallet", "sales.add_sale"),
        item("New sale", "pos-cart", "cart", "sales.add_sale"),
        item("Sales register", "module-overview", "chart", "sales.view_sale", ("sales",)),
        item("Payments", "module-overview", "wallet", "payments.view_payment", ("payments",)),
        item("Cashier sessions", "module-overview", "wallet", "pos.view_possession", ("sessions",)),
        item("Returns and refunds", "module-overview", "box", "sales.view_salereturn", ("returns",)),
    )),
    ("Inventory", "box", (
        item("Products", "module-overview", "box", "catalog.view_product", ("products",)),
        item("Add product", "product-create", "sparkles", "catalog.add_product"),
        item("Stock levels", "module-overview", "chart", "inventory.view_stockbalance", ("stock",)),
        item("Serialized inventory", "module-overview", "box", "inventory.view_stockunit", ("inventory",)),
        item("Stock movements", "module-overview", "truck", "inventory.view_stockmovement", ("movements",)),
        item("Stock adjustment", "stock-adjustment-create", "wrench", "inventory.add_stockadjustment"),
        item("Transfers", "module-overview", "truck", "transfers.view_stocktransfer", ("transfers",)),
    )),
    ("Purchasing", "truck", (
        item("New purchase", "purchase-create", "sparkles", "purchasing.add_purchaseorder"),
        item("Purchase register", "module-overview", "chart", "purchasing.view_purchaseorder", ("purchases",)),
        item("Purchase discrepancies", "module-overview", "wrench", "purchasing.view_purchasediscrepancy", ("purchase-discrepancies",)),
        item("Supplier return", "supplier-return-create", "truck", "purchasing.add_supplierreturn"),
        item("Supplier returns", "module-overview", "chart", "purchasing.view_supplierreturn", ("supplier-returns",)),
        item("Payables", "module-overview", "wallet", "purchasing.view_purchaseorder", ("payables",)),
    )),
    ("Customers & Finance", "wallet", (
        item("Customers and suppliers", "module-overview", "wallet", "contacts.view_contact", ("contacts",)),
        item("Add customer or supplier", "contact-create", "sparkles", "contacts.add_contact"),
        item("Receivables", "module-overview", "wallet", "sales.view_sale", ("receivables",)),
        item("Expenses", "module-overview", "chart", "expenses.view_expense", ("expenses",)),
        item("New expense", "expense-create", "sparkles", "expenses.add_expense"),
        item("Commissions", "module-overview", "chart", "commissions.view_commissionaccrual", ("commissions",)),
        item("Commission payouts", "module-overview", "wallet", owner_only=True, args=("commission-payouts",), requestable=False),
    )),
    ("Repairs & Warranty", "wrench", (
        item("New repair", "repair-create", "sparkles", "repairs.add_repairticket"),
        item("Repair register", "module-overview", "wrench", "repairs.view_repairticket", ("repairs",)),
    )),
    ("Reports", "chart", (
        item("Operational report", "operational-report", "chart"),
        item("Retail analytics", "retail-analytics-report", "chart"),
        item("IMEI history", "imei-history", "box", "inventory.view_stockunit"),
        item("Aged stock", "module-overview", "chart", "inventory.view_stockunit", ("aged-stock",)),
        item("Exception and aging report", "exception-report", "chart", owner_only=True, requestable=False),
    )),
    ("Administration", "cog", (
        item("Users and access", "membership-access-list", "cog", owner_only=True, requestable=False),
        item("Roles and permissions", "module-overview", "lock", owner_only=True, args=("roles",), requestable=False),
        item("Approval policies", "approval-policy-list", "lock", owner_only=True, requestable=False),
        item("Branches", "module-overview", "box", owner_only=True, args=("branches",), requestable=False),
        item("Locations", "module-overview", "box", owner_only=True, args=("locations",), requestable=False),
        item("Subscriptions", "module-overview", "wallet", owner_only=True, args=("subscriptions",), requestable=False),
        item("Integrations", "module-overview", "cog", owner_only=True, args=("integrations",), requestable=False),
        item("Django administration", "admin:index", "cog", requestable=False, staff_only=True),
    )),
)


def build_navigation(request):
    organization = getattr(request, "organization", None)
    membership = getattr(request, "membership", None)
    privileged = bool(
        request.user.is_superuser
        or request.user.is_platform_admin
        or (membership and membership.is_owner)
    )
    active_name = request.resolver_match.url_name if request.resolver_match else ""
    active_args = tuple(request.resolver_match.kwargs.values()) if request.resolver_match else ()
    groups = []
    for label, icon, configured_items in NAVIGATION_GROUPS:
        items = []
        for configured in configured_items:
            nav_item = configured.copy()
            nav_item["url"] = reverse(nav_item["url_name"], args=nav_item["args"])
            if nav_item["staff_only"]:
                nav_item["enabled"] = request.user.is_staff
                nav_item["locked_reason"] = "Django administration is restricted to platform staff."
            elif nav_item["owner_only"]:
                nav_item["enabled"] = privileged
                nav_item["locked_reason"] = "Organization owner access is required."
            elif nav_item["permission"]:
                nav_item["enabled"] = bool(
                    organization
                    and user_has_organization_permission(
                        request.user, organization, nav_item["permission"]
                    )
                )
                nav_item["locked_reason"] = f"Request {nav_item['label']} access from an administrator."
            else:
                nav_item["enabled"] = True
                nav_item["locked_reason"] = ""
            nav_item["active"] = active_name == nav_item["url_name"] and (
                not nav_item["args"] or active_args == nav_item["args"]
            )
            items.append(nav_item)
        groups.append({
            "label": label,
            "icon_path": ICONS[icon],
            "items": items,
            "active": any(nav_item["active"] for nav_item in items),
        })
    return groups
