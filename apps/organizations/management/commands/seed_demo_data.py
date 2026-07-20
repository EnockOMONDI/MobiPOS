from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record_audit_event
from apps.catalog.models import Brand, Category, Product
from apps.commissions.models import CommissionAccrual, CommissionPayout, CommissionPayoutLine, CommissionPayoutStatus, CommissionRule
from apps.contacts.models import Contact, ContactType
from apps.expenses.models import Expense, ExpenseStatus
from apps.integrations.services import queue_integration_event
from apps.inventory.models import SerialStatus, StockMovementType, StockUnit
from apps.inventory.services import post_stock_movement
from apps.notifications.models import Notification
from apps.operations.models import InstallmentStatus, Payable, Receivable, ReceivableInstallment
from apps.payments.models import Payment, PaymentMethod, PaymentStatus, Refund
from apps.pos.models import POSSession, SessionStatus
from apps.purchasing.models import (
    PurchaseDiscrepancy,
    PurchaseDiscrepancyStatus,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseStatus,
    SupplierReturn,
    SupplierReturnStatus,
)
from apps.repairs.models import RepairPartUsage, RepairStatus, RepairTicket
from apps.sales.models import ReturnDisposition, ReturnOutcome, ReturnStatus, Sale, SaleLine, SaleReturn, SaleReturnLine, SaleStatus
from apps.sales.services import calculate_sale_line_amounts
from apps.transfers.models import DiscrepancyStatus, StockTransfer, StockTransferLine, TransferDiscrepancy, TransferStatus
from apps.organizations.models import (
    Announcement,
    AgentProfile,
    AgentProfileStatus,
    AgentProfileType,
    Branch,
    Company,
    Location,
    LocationType,
    Membership,
    MembershipStatus,
    Organization,
    OrganizationSetting,
    OrganizationStatus,
    Plan,
    Role,
    Subscription,
    SubscriptionInvoice,
    SubscriptionInvoiceStatus,
    SubscriptionStatus,
)

DEMO_PASSWORD = "DemoPass123!"
ADMIN_PASSWORD = "AdminPass123!"

DEMO_ORGANIZATIONS = (
    {
        "organization": {
            "name": "Nairobi Mobile Hub",
            "slug": "nairobi-mobile-hub",
            "email": "hello@nairobi-mobile-hub.test",
            "phone_number": "+254700000201",
        },
        "owner": {
            "username": "brian",
            "email": "brian@nairobi-mobile-hub.test",
            "first_name": "Brian",
            "last_name": "Otieno",
            "phone_number": "+254700000211",
        },
        "company": {
            "name": "Nairobi Mobile Hub Limited",
            "code": "NMH",
            "legal_name": "Nairobi Mobile Hub Limited",
            "tax_number": "P051234568B",
        },
        "branch": {
            "name": "Westlands Branch",
            "code": "WST",
            "email": "westlands@nairobi-mobile-hub.test",
            "phone_number": "+254700000221",
        },
    },
)


class Command(BaseCommand):
    help = "Create repeatable local demo users, organizations, and operational sample data."

    @transaction.atomic
    def handle(self, *args, **options):
        plan, _ = Plan.objects.update_or_create(
            code="growth",
            defaults={
                "name": "Growth",
                "monthly_price": Decimal("7500.00"),
                "limits": {"users": 25, "branches": 5, "pos_locations": 10},
                "is_active": True,
            },
        )
        admin = self._upsert_user(
            username="platformadmin",
            email="admin@mobipos.test",
            password=ADMIN_PASSWORD,
            first_name="Platform",
            last_name="Administrator",
            phone_number="+254700000001",
            is_staff=True,
            is_superuser=True,
            is_platform_admin=True,
            is_demo_account=True,
        )

        for dataset in DEMO_ORGANIZATIONS:
            self._seed_organization(dataset, plan, admin)

        self.stdout.write(self.style.SUCCESS("Demo data is ready."))
        self.stdout.write("")
        self.stdout.write("Platform admin: platformadmin / AdminPass123!")
        self.stdout.write("Demo owner: brian / DemoPass123!")
        self.stdout.write("Extra demo staff use DemoPass123!: manager, cashier, agent, dsa, inventory, technician")

    def _seed_organization(self, dataset, plan, admin):
        organization_values = dataset["organization"]
        organization, _ = Organization.objects.update_or_create(
            slug=organization_values["slug"],
            defaults={
                **organization_values,
                "status": OrganizationStatus.ACTIVE,
                "currency": "KES",
                "timezone": "Africa/Nairobi",
            },
        )
        owner_values = dataset["owner"]
        owner = self._upsert_user(
            **owner_values,
            password=DEMO_PASSWORD,
            is_staff=False,
            is_superuser=False,
            is_platform_admin=False,
            is_demo_account=True,
        )

        company_values = dataset["company"]
        company, _ = Company.objects.update_or_create(
            organization=organization,
            code=company_values["code"],
            defaults={**company_values, "is_active": True},
        )
        branch_values = dataset["branch"]
        branch, _ = Branch.objects.update_or_create(
            organization=organization,
            code=branch_values["code"],
            defaults={**branch_values, "company": company, "is_active": True},
        )
        Location.objects.update_or_create(
            organization=organization,
            code=f"{branch.code}-WH",
            defaults={
                "branch": branch,
                "name": f"{branch.name} Warehouse",
                "location_type": LocationType.WAREHOUSE,
                "is_active": True,
            },
        )
        Location.objects.update_or_create(
            organization=organization,
            code=f"{branch.code}-POS",
            defaults={
                "branch": branch,
                "name": f"{branch.name} POS",
                "location_type": LocationType.POS,
                "is_active": True,
            },
        )

        role, _ = Role.objects.update_or_create(
            organization=organization,
            code="organization-owner",
            defaults={
                "name": "Organization Owner",
                "description": "Full operational access for the demo organization.",
                "is_active": True,
            },
        )
        role.permissions.set(
            Permission.objects.filter(
                content_type__app_label__in=("organizations", "accounts", "audit")
            )
        )
        membership, _ = Membership.objects.update_or_create(
            organization=organization,
            user=owner,
            defaults={"status": MembershipStatus.ACTIVE, "is_owner": True},
        )
        membership.roles.set([role])
        membership.branches.set([branch])

        today = timezone.localdate()
        subscription, _ = Subscription.objects.update_or_create(
            organization=organization,
            plan=plan,
            defaults={
                "status": SubscriptionStatus.ACTIVE,
                "starts_on": today,
                "renews_on": today + timedelta(days=30),
                "grace_ends_on": today + timedelta(days=37),
            },
        )
        SubscriptionInvoice.objects.update_or_create(
            organization=organization,
            subscription=subscription,
            number=f"DEMO-SUB-{str(organization.id)[:8].upper()}",
            defaults={
                "amount": plan.monthly_price,
                "status": SubscriptionInvoiceStatus.PAID,
                "due_on": today,
                "paid_at": timezone.now(),
                "payment_reference": f"DEMO-SUB-PAY-{str(organization.id)[:6].upper()}",
            },
        )
        OrganizationSetting.objects.update_or_create(
            organization=organization,
            key="receipt-settings",
            defaults={
                "description": "Default receipt and customer service details.",
                "value": {
                    "footer": f"Thank you for shopping with {organization.name}.",
                    "show_tax": True,
                },
            },
        )
        Announcement.objects.update_or_create(
            organization=organization,
            title="Welcome to MobiPOS",
            defaults={
                "body": (
                    f"<p>{organization.name} is configured with a branch, warehouse, "
                    "POS location, owner role, and active subscription.</p>"
                ),
                "starts_at": timezone.now(),
                "is_active": True,
            },
        )
        if not organization.auditevent_set.filter(action="demo.organization_seeded").exists():
            record_audit_event(
                action="demo.organization_seeded",
                actor=admin,
                organization=organization,
                target=organization,
                message="Created organization demo data.",
            )
        self._seed_operations(organization, owner, branch)

    def _seed_operations(self, organization, owner, branch):
        warehouse = organization.location_set.get(code=f"{branch.code}-WH")
        pos_location = organization.location_set.get(code=f"{branch.code}-POS")
        demo_users = self._seed_demo_team(organization, branch, owner)
        manager = demo_users["manager"]
        cashier = demo_users["cashier"]
        agent = demo_users["agent"]
        dsa = demo_users["dsa"]
        agent_membership = demo_users["agent_membership"]
        agent_profile = demo_users["agent_profile"]
        dsa_profile = demo_users["dsa_profile"]
        agent_location, _ = Location.objects.update_or_create(
            organization=organization,
            code=f"{branch.code}-AGT",
            defaults={
                "branch": branch,
                "name": f"{agent.get_full_name() or agent.username} Agent Custody",
                "location_type": LocationType.AGENT,
                "custodian_membership": agent_membership,
                "is_active": True,
            },
        )
        phones, _ = Category.objects.update_or_create(
            organization=organization, code="phones", defaults={"name": "Phones", "is_active": True}
        )
        accessories, _ = Category.objects.update_or_create(
            organization=organization, code="accessories", defaults={"name": "Accessories", "is_active": True}
        )
        brand, _ = Brand.objects.update_or_create(
            organization=organization, name="MobiPOS Mobile", defaults={"is_active": True}
        )
        phone, _ = Product.objects.update_or_create(
            organization=organization, sku="MP-A07-64",
            defaults={
                "category": phones, "brand": brand, "name": "A07 64GB/4GB",
                "barcode": f"{organization.slug}-PHONE", "is_serialized": True,
                "image_url": "/static/img/products/a07-phone.svg",
                "warranty_days": 365, "cost_price": Decimal("15000.00"),
                "selling_price": Decimal("18600.00"), "tax_rate": Decimal("16.00"),
            },
        )
        premium_phone, _ = Product.objects.update_or_create(
            organization=organization, sku="MP-S24-256",
            defaults={
                "category": phones, "brand": brand, "name": "S24 Ultra 256GB",
                "barcode": f"{organization.slug}-S24", "is_serialized": True,
                "image_url": "/static/img/products/s24-phone.svg",
                "warranty_days": 365, "cost_price": Decimal("118000.00"),
                "selling_price": Decimal("139500.00"), "tax_rate": Decimal("16.00"),
            },
        )
        charger, _ = Product.objects.update_or_create(
            organization=organization, sku="CHG-20W",
            defaults={
                "category": accessories, "brand": brand, "name": "20W Fast Charger",
                "barcode": f"{organization.slug}-CHARGER", "is_serialized": False,
                "image_url": "/static/img/products/charger-20w.svg",
                "warranty_days": 90, "cost_price": Decimal("850.00"),
                "selling_price": Decimal("1500.00"), "tax_rate": Decimal("16.00"),
            },
        )
        earbuds, _ = Product.objects.update_or_create(
            organization=organization, sku="EAR-PRO",
            defaults={
                "category": accessories, "brand": brand, "name": "Wireless Earbuds Pro",
                "barcode": f"{organization.slug}-EARBUDS", "is_serialized": False,
                "image_url": "/static/img/products/earbuds-pro.svg",
                "warranty_days": 180, "cost_price": Decimal("2400.00"),
                "selling_price": Decimal("4200.00"), "tax_rate": Decimal("16.00"),
            },
        )
        customer, _ = Contact.objects.update_or_create(
            organization=organization, phone_number=f"{organization.phone_number}9",
            defaults={
                "contact_type": ContactType.CUSTOMER, "name": "Demo Credit Customer",
                "email": f"customer@{organization.slug}.test", "credit_limit": Decimal("50000.00"),
                "payment_terms_days": 30,
            },
        )
        cash_customer, _ = Contact.objects.update_or_create(
            organization=organization, phone_number=f"{organization.phone_number}7",
            defaults={
                "contact_type": ContactType.CUSTOMER, "name": "Demo Walk-in Customer",
                "email": f"walkin@{organization.slug}.test", "credit_limit": Decimal("0.00"),
                "payment_terms_days": 0,
            },
        )
        supplier, _ = Contact.objects.update_or_create(
            organization=organization, phone_number=f"{organization.phone_number}8",
            defaults={
                "contact_type": ContactType.SUPPLIER, "name": "Demo Device Supplier",
                "email": f"supplier@{organization.slug}.test",
            },
        )
        serial_prefix = str(organization.id).replace("-", "")[:8]
        phone_units = self._seed_serial_units(
            organization=organization,
            product=phone,
            location=pos_location,
            serial_prefix=serial_prefix,
            serial_start=1,
            count=6,
            actor=owner,
        )
        premium_units = self._seed_serial_units(
            organization=organization,
            product=premium_phone,
            location=warehouse,
            serial_prefix=serial_prefix,
            serial_start=101,
            count=4,
            actor=manager,
        )
        agent_unit = phone_units[1]
        if agent_unit.location_id != agent_location.id or agent_unit.status != SerialStatus.AVAILABLE:
            agent_unit.location = agent_location
            agent_unit.status = SerialStatus.AVAILABLE
            agent_unit.save(update_fields=["location", "status", "updated_at"])
            post_stock_movement(
                organization=organization, product=phone, location=agent_location, quantity=1,
                movement_type=StockMovementType.TRANSFER_RECEIPT, actor=agent, stock_unit=agent_unit,
                unit_cost=phone.cost_price, reason="Demo agent allocation",
            )
        sold_unit = phone_units[2]
        if sold_unit.status != SerialStatus.SOLD:
            sold_unit.status = SerialStatus.SOLD
            sold_unit.save(update_fields=["status", "updated_at"])
            post_stock_movement(
                organization=organization, product=phone, location=pos_location, quantity=-1,
                movement_type=StockMovementType.SALE, actor=cashier, stock_unit=sold_unit,
                unit_cost=phone.cost_price, reason="Demo serialized phone sale",
            )
        damaged_unit = premium_units[0]
        if damaged_unit.status != SerialStatus.DAMAGED:
            damaged_unit.status = SerialStatus.DAMAGED
            damaged_unit.save(update_fields=["status", "updated_at"])
        if not charger.balances.filter(location=pos_location).exists():
            post_stock_movement(
                organization=organization, product=charger, location=pos_location, quantity=25,
                movement_type=StockMovementType.OPENING, actor=owner, unit_cost=charger.cost_price,
                reason="Demo opening stock",
            )
        if not earbuds.balances.filter(location=warehouse).exists():
            post_stock_movement(
                organization=organization, product=earbuds, location=warehouse, quantity=40,
                movement_type=StockMovementType.OPENING, actor=manager, unit_cost=earbuds.cost_price,
                reason="Demo warehouse accessory stock",
            )
        purchase, _ = PurchaseOrder.objects.update_or_create(
            organization=organization,
            number="DEMO-PO-001",
            defaults={
                "supplier": supplier,
                "destination": warehouse,
                "status": PurchaseStatus.PART_RECEIVED,
                "ordered_on": timezone.localdate() - timedelta(days=5),
                "notes": "Demo supplier order for premium devices and accessories.",
                "created_by": manager,
            },
        )
        PurchaseOrderLine.objects.update_or_create(
            organization=organization,
            order=purchase,
            product=premium_phone,
            defaults={"quantity": Decimal("6"), "received_quantity": Decimal("4"), "unit_cost": premium_phone.cost_price},
        )
        PurchaseOrderLine.objects.update_or_create(
            organization=organization,
            order=purchase,
            product=earbuds,
            defaults={"quantity": Decimal("60"), "received_quantity": Decimal("40"), "unit_cost": earbuds.cost_price},
        )
        transfer, _ = StockTransfer.objects.update_or_create(
            organization=organization,
            number="DEMO-TRF-001",
            defaults={
                "source": warehouse,
                "destination": pos_location,
                "status": TransferStatus.IN_TRANSIT,
                "requested_by": manager,
                "approved_by": owner,
                "notes": "Demo warehouse to branch transfer awaiting receipt.",
            },
        )
        StockTransferLine.objects.update_or_create(
            organization=organization,
            transfer=transfer,
            product=premium_phone,
            stock_unit=premium_units[1],
            defaults={"quantity": Decimal("1"), "received_quantity": Decimal("0")},
        )
        StockTransferLine.objects.update_or_create(
            organization=organization,
            transfer=transfer,
            product=earbuds,
            defaults={"quantity": Decimal("10"), "received_quantity": Decimal("0")},
        )
        session, _ = POSSession.objects.update_or_create(
            organization=organization, number="DEMO-SESSION-001",
            defaults={
                "location": pos_location, "cashier": cashier, "status": SessionStatus.OPEN,
                "opening_float": Decimal("5000.00"), "expected_cash": Decimal("20100.00"),
            },
        )
        sale_amounts = calculate_sale_line_amounts(product=charger, quantity=Decimal("1"))
        sale, _ = Sale.objects.update_or_create(
            organization=organization, number="DEMO-SALE-001",
            defaults={
                "session": session, "location": pos_location, "customer": customer, "agent": agent,
                "status": SaleStatus.PAID, "subtotal": sale_amounts["gross"],
                "tax_total": sale_amounts["tax"], "total": sale_amounts["total"],
                "paid_total": sale_amounts["total"], "created_by": cashier, "completed_at": timezone.now(),
            },
        )
        SaleLine.objects.update_or_create(
            organization=organization, sale=sale, product=charger,
            defaults={
                "quantity": 1, "unit_price": charger.selling_price, "unit_cost": charger.cost_price,
                "tax": sale_amounts["tax"], "line_total": sale_amounts["gross"],
            },
        )
        Payment.objects.update_or_create(
            organization=organization, number="DEMO-PAY-001",
            defaults={
                "customer": customer, "sale": sale, "method": PaymentMethod.MPESA,
                "status": PaymentStatus.CONFIRMED, "amount": sale.total,
                "provider_reference": f"DEMO{str(organization.id)[:6].upper()}", "received_by": cashier,
            },
        )
        phone_amounts = calculate_sale_line_amounts(product=phone, quantity=Decimal("1"))
        phone_sale, _ = Sale.objects.update_or_create(
            organization=organization,
            number="DEMO-SALE-002",
            defaults={
                "session": session, "location": pos_location, "customer": cash_customer, "agent": dsa,
                "status": SaleStatus.PAID, "subtotal": phone_amounts["gross"],
                "tax_total": phone_amounts["tax"], "total": phone_amounts["total"],
                "paid_total": phone_amounts["total"], "created_by": cashier, "completed_at": timezone.now(),
            },
        )
        SaleLine.objects.update_or_create(
            organization=organization, sale=phone_sale, product=phone, stock_unit=sold_unit,
            defaults={
                "quantity": 1, "unit_price": phone.selling_price, "unit_cost": phone.cost_price,
                "tax": phone_amounts["tax"], "line_total": phone_amounts["gross"],
            },
        )
        Payment.objects.update_or_create(
            organization=organization, number="DEMO-PAY-002",
            defaults={
                "customer": cash_customer, "sale": phone_sale, "method": PaymentMethod.CASH,
                "status": PaymentStatus.CONFIRMED, "amount": phone_sale.total,
                "provider_reference": "", "received_by": cashier,
            },
        )
        credit_amounts = calculate_sale_line_amounts(product=earbuds, quantity=Decimal("2"))
        credit_sale, _ = Sale.objects.update_or_create(
            organization=organization,
            number="DEMO-SALE-003",
            defaults={
                "session": session, "location": pos_location, "customer": customer, "agent": agent,
                "status": SaleStatus.PART_PAID, "subtotal": credit_amounts["gross"],
                "tax_total": credit_amounts["tax"], "total": credit_amounts["total"],
                "paid_total": Decimal("3000.00"), "created_by": cashier, "completed_at": timezone.now(),
                "sale_channel": "credit", "customer_national_id": "12345678",
                "next_of_kin_name": "Demo Next of Kin", "next_of_kin_phone": "+254711000999",
                "due_on": timezone.localdate() + timedelta(days=14),
            },
        )
        SaleLine.objects.update_or_create(
            organization=organization, sale=credit_sale, product=earbuds,
            defaults={
                "quantity": 2, "unit_price": earbuds.selling_price, "unit_cost": earbuds.cost_price,
                "tax": credit_amounts["tax"], "line_total": credit_amounts["gross"],
            },
        )
        Payment.objects.update_or_create(
            organization=organization, number="DEMO-PAY-003",
            defaults={
                "customer": customer, "sale": credit_sale, "method": PaymentMethod.CREDIT,
                "status": PaymentStatus.PENDING, "amount": credit_sale.total - credit_sale.paid_total,
                "provider_reference": "", "received_by": cashier,
            },
        )
        rule, _ = CommissionRule.objects.update_or_create(
            organization=organization, name="Default sales commission",
            defaults={"percentage": Decimal("2.50"), "requires_full_payment": True, "is_active": True},
        )
        CommissionAccrual.objects.update_or_create(
            organization=organization, sale=sale, agent=agent, rule=rule,
            defaults={"amount": Decimal("37.50"), "is_payable": True},
        )
        CommissionAccrual.objects.update_or_create(
            organization=organization, sale=sale, agent=owner, rule=rule,
            defaults={"amount": Decimal("37.50"), "is_payable": True},
        )
        CommissionAccrual.objects.update_or_create(
            organization=organization, sale=phone_sale, agent=dsa, rule=rule,
            defaults={"amount": Decimal("465.00"), "is_payable": True},
        )
        Expense.objects.update_or_create(
            organization=organization, number="DEMO-EXP-001",
            defaults={
                "branch": branch, "category": "Utilities", "description": "Demo internet expense",
                "amount": Decimal("3500.00"), "status": ExpenseStatus.APPROVED,
                "incurred_on": timezone.localdate(), "requested_by": manager, "approved_by": owner,
            },
        )
        Expense.objects.update_or_create(
            organization=organization, number="DEMO-EXP-002",
            defaults={
                "branch": branch, "category": "Transport", "description": "Demo agent field transport reimbursement",
                "amount": Decimal("1800.00"), "status": ExpenseStatus.SUBMITTED,
                "incurred_on": timezone.localdate(), "requested_by": agent, "approved_by": None,
            },
        )
        RepairTicket.objects.update_or_create(
            organization=organization, number="DEMO-REP-001",
            defaults={
                "branch": branch, "customer": customer, "status": RepairStatus.DIAGNOSING,
                "issue": "Device does not charge consistently.", "technician": owner,
                "quoted_amount": Decimal("2500.00"),
            },
        )
        Notification.objects.update_or_create(
            organization=organization, recipient=owner, title="Demo organization ready",
            defaults={"message": "Products, inventory, sales, payments, expenses, and repairs are ready to review.", "link": "/"},
        )
        Notification.objects.update_or_create(
            organization=organization, recipient=manager, title="Pending transfer receipt",
            defaults={"message": "Demo warehouse transfer DEMO-TRF-001 is waiting for branch receipt.", "link": "/transfers/"},
        )
        Notification.objects.update_or_create(
            organization=organization, recipient=agent, title="Agent stock allocated",
            defaults={"message": f"{agent_unit.serial_number} is assigned to your agent custody location.", "link": "/reports/agent-network/"},
        )
        self._seed_rich_scenarios(
            organization=organization,
            owner=owner,
            branch=branch,
            warehouse=warehouse,
            pos_location=pos_location,
            agent_location=agent_location,
            products={
                "phone": phone,
                "premium_phone": premium_phone,
                "charger": charger,
                "earbuds": earbuds,
            },
            contacts={
                "credit_customer": customer,
                "cash_customer": cash_customer,
                "supplier": supplier,
            },
            users=demo_users,
        )
        queue_integration_event(
            organization=organization, provider="etims", event_type="invoice.submit",
            idempotency_key=f"demo-etims-{organization.id}", payload={"sale_number": sale.number},
        )
        self._record_demo_activity(organization, owner, manager, cashier, agent, dsa, agent_profile, dsa_profile)

    def _seed_demo_team(self, organization, branch, owner):
        role_definitions = {
            "manager": ("Branch Manager", "branch-manager", "Branch-level stock, sales, and approval oversight."),
            "cashier": ("Cashier", "cashier", "POS sales, receipts, customer payments, and daily sessions."),
            "agent": ("Field Agent", "field-agent", "Agent custody, allocated stock, and assisted sales."),
            "dsa": ("Direct Sales Agent", "dsa", "Sub-agent sales activity linked to a supervising agent."),
            "inventory": ("Inventory Officer", "inventory-officer", "Purchasing, receiving, stock intake, and transfer control."),
            "technician": ("Repair Technician", "repair-technician", "Warranty intake, diagnosis, parts usage, and repair closure."),
        }
        roles = {}
        permissions = Permission.objects.filter(
            content_type__app_label__in=(
                "organizations", "catalog", "contacts", "inventory", "purchasing",
                "transfers", "sales", "pos", "payments", "commissions", "expenses",
                "operations", "repairs", "reports", "notifications", "audit",
            )
        )
        for key, (name, code, description) in role_definitions.items():
            role, _ = Role.objects.update_or_create(
                organization=organization,
                code=code,
                defaults={"name": name, "description": description, "is_active": True},
            )
            if key == "manager":
                role.permissions.set(permissions)
            elif key == "cashier":
                role.permissions.set(permissions.filter(content_type__app_label__in=("contacts", "sales", "pos", "payments", "notifications")))
            elif key == "agent":
                role.permissions.set(permissions.filter(content_type__app_label__in=("sales", "inventory", "transfers", "reports", "notifications")))
            elif key == "dsa":
                role.permissions.set(permissions.filter(content_type__app_label__in=("sales", "notifications")))
            elif key == "inventory":
                role.permissions.set(permissions.filter(content_type__app_label__in=("catalog", "inventory", "purchasing", "transfers", "reports", "notifications")))
            else:
                role.permissions.set(permissions.filter(content_type__app_label__in=("repairs", "inventory", "reports", "notifications")))
            roles[key] = role

        user_specs = {
            "manager": ("manager", "Mary", "Mwangi", "+254700100001"),
            "cashier": ("cashier", "Kevin", "Otieno", "+254700100002"),
            "agent": ("agent", "Renny", "Kiptoo", "+254700100003"),
            "dsa": ("dsa", "Faith", "Achieng", "+254700100004"),
            "inventory": ("inventory", "Grace", "Njeri", "+254700100005"),
            "technician": ("technician", "Daniel", "Kariuki", "+254700100006"),
        }
        team = {}
        for key, (suffix, first_name, last_name, phone_number) in user_specs.items():
            user = self._upsert_user(
                username=f"{organization.slug}-{suffix}",
                email=f"{suffix}@{organization.slug}.test",
                password=DEMO_PASSWORD,
                first_name=first_name,
                last_name=last_name,
                phone_number=phone_number,
                is_staff=False,
                is_superuser=False,
                is_platform_admin=False,
                is_demo_account=True,
            )
            membership, _ = Membership.objects.update_or_create(
                organization=organization,
                user=user,
                defaults={"status": MembershipStatus.ACTIVE, "is_owner": False},
            )
            membership.roles.set([roles[key]])
            membership.branches.set([branch])
            team[key] = user
            team[f"{key}_membership"] = membership

        agent_profile, _ = AgentProfile.objects.update_or_create(
            organization=organization,
            membership=team["agent_membership"],
            defaults={
                "profile_type": AgentProfileType.AGENT,
                "status": AgentProfileStatus.ACTIVE,
                "branch": branch,
                "legal_name": team["agent"].get_full_name(),
                "national_id_number": f"AG{str(organization.id).replace('-', '')[:6]}",
                "phone_number": team["agent"].phone_number,
                "registration_notes": "Demo agent onboarded with National ID capture pending file upload.",
                "verification_notes": "Demo verification approved for field stock allocation.",
                "registered_by": owner,
                "verified_by": owner,
                "verified_at": timezone.now(),
            },
        )
        dsa_profile, _ = AgentProfile.objects.update_or_create(
            organization=organization,
            membership=team["dsa_membership"],
            defaults={
                "profile_type": AgentProfileType.DSA,
                "status": AgentProfileStatus.ACTIVE,
                "branch": branch,
                "supervisor": agent_profile,
                "legal_name": team["dsa"].get_full_name(),
                "national_id_number": f"DS{str(organization.id).replace('-', '')[:6]}",
                "phone_number": team["dsa"].phone_number,
                "registration_notes": "Demo DSA registered under supervising field agent.",
                "verification_notes": "Demo DSA verification approved.",
                "registered_by": team["agent"],
                "verified_by": owner,
                "verified_at": timezone.now(),
            },
        )
        team["agent_profile"] = agent_profile
        team["dsa_profile"] = dsa_profile
        return team

    def _seed_serial_units(self, *, organization, product, location, serial_prefix, serial_start, count, actor):
        units = []
        for offset in range(count):
            serial = f"{serial_prefix}{serial_start + offset:09d}"
            stock_unit, created = StockUnit.objects.get_or_create(
                organization=organization,
                serial_number=serial,
                defaults={
                    "product": product,
                    "location": location,
                    "status": SerialStatus.AVAILABLE,
                    "unit_cost": product.cost_price,
                    "warranty_expires_on": timezone.localdate() + timedelta(days=product.warranty_days),
                },
            )
            units.append(stock_unit)
            if created:
                post_stock_movement(
                    organization=organization, product=product, location=location, quantity=1,
                    movement_type=StockMovementType.OPENING, actor=actor, stock_unit=stock_unit,
                    unit_cost=product.cost_price, reason="Demo serialized opening stock",
                )
        return units

    def _seed_rich_scenarios(self, *, organization, owner, branch, warehouse, pos_location, agent_location, products, contacts, users):
        today = timezone.localdate()
        now = timezone.now()
        manager = users["manager"]
        cashier = users["cashier"]
        agent = users["agent"]
        dsa = users["dsa"]
        inventory = users["inventory"]
        technician = users["technician"]

        company = branch.company
        cbd_branch, _ = Branch.objects.update_or_create(
            organization=organization,
            code="NBO-CBD",
            defaults={
                "company": company,
                "name": "Nairobi CBD Branch",
                "email": "cbd@nairobi-mobile-hub.test",
                "phone_number": "+254700000231",
                "is_active": True,
            },
        )
        trm_branch, _ = Branch.objects.update_or_create(
            organization=organization,
            code="TRM",
            defaults={
                "company": company,
                "name": "Thika Road Mall Branch",
                "email": "trm@nairobi-mobile-hub.test",
                "phone_number": "+254700000232",
                "is_active": True,
            },
        )
        cbd_pos = self._location(organization, cbd_branch, "NBO-CBD-POS", "Nairobi CBD POS", LocationType.POS)
        trm_pos = self._location(organization, trm_branch, "TRM-POS", "Thika Road Mall POS", LocationType.POS)
        self._location(organization, cbd_branch, "NBO-CBD-WH", "Nairobi CBD Mini Warehouse", LocationType.WAREHOUSE)
        self._location(organization, trm_branch, "TRM-WH", "Thika Road Mall Stock Room", LocationType.WAREHOUSE)
        repair_location = self._location(organization, branch, "WST-REPAIR", "Westlands Repair Bench", LocationType.REPAIR)

        for membership in (users["manager_membership"], users["cashier_membership"], users["inventory_membership"], users["technician_membership"]):
            membership.branches.set([branch, cbd_branch, trm_branch])

        phones = products["phone"].category
        accessories = products["charger"].category
        brands = {}
        for brand_name in ("Samsung", "Xiaomi", "Apple", "Tecno", "Itel", "Oraimo"):
            brands[brand_name], _ = Brand.objects.update_or_create(
                organization=organization,
                name=brand_name,
                defaults={"is_active": True},
            )

        extra_products = {
            "redmi": self._product(
                organization, phones, brands["Xiaomi"], "REDMI-N13", "Redmi Note 13 128GB",
                True, Decimal("21500.00"), Decimal("27500.00"), "/static/img/products/redmi-note.svg"
            ),
            "iphone": self._product(
                organization, phones, brands["Apple"], "IPH-13-128", "iPhone 13 128GB Certified Pre-Owned",
                True, Decimal("58000.00"), Decimal("69900.00"), "/static/img/products/iphone-13.svg"
            ),
            "tecno": self._product(
                organization, phones, brands["Tecno"], "TEC-SPARK-20", "Tecno Spark 20",
                True, Decimal("14500.00"), Decimal("18900.00"), "/static/img/products/tecno-spark.svg"
            ),
            "itel": self._product(
                organization, phones, brands["Itel"], "ITEL-A70", "Itel A70",
                True, Decimal("9300.00"), Decimal("12400.00"), "/static/img/products/itel-a70.svg"
            ),
            "cable": self._product(
                organization, accessories, brands["Oraimo"], "CABLE-TC", "Type-C Fast Charging Cable",
                False, Decimal("250.00"), Decimal("650.00"), "/static/img/products/type-c-cable.svg"
            ),
            "glass": self._product(
                organization, accessories, brands["MobiPOS Mobile"] if "MobiPOS Mobile" in brands else products["charger"].brand,
                "SCREEN-GLASS", "Tempered Screen Protector",
                False, Decimal("120.00"), Decimal("500.00"), "/static/img/products/screen-glass.svg"
            ),
            "case": self._product(
                organization, accessories, products["charger"].brand, "CASE-SHOCK", "Shockproof Phone Case",
                False, Decimal("300.00"), Decimal("900.00"), "/static/img/products/phone-case.svg"
            ),
            "powerbank": self._product(
                organization, accessories, brands["Oraimo"], "POWER-10K", "10,000mAh Power Bank",
                False, Decimal("1800.00"), Decimal("3200.00"), "/static/img/products/powerbank.svg"
            ),
        }
        all_products = {**products, **extra_products}

        customers = self._seed_customers(organization)
        suppliers = self._seed_suppliers(organization, contacts["supplier"])

        serial_prefix = str(organization.id).replace("-", "")[:8]
        serial_pool = []
        for index, (key, product) in enumerate(
            (
                ("phone", products["phone"]),
                ("premium_phone", products["premium_phone"]),
                ("redmi", extra_products["redmi"]),
                ("iphone", extra_products["iphone"]),
                ("tecno", extra_products["tecno"]),
                ("itel", extra_products["itel"]),
            ),
            start=2,
        ):
            target = [pos_location, cbd_pos, trm_pos][index % 3]
            serial_pool.extend(
                self._seed_serial_units(
                    organization=organization,
                    product=product,
                    location=target,
                    serial_prefix=f"{serial_prefix}{index}",
                    serial_start=1,
                    count=10,
                    actor=inventory,
                )
            )
        for product in (products["charger"], products["earbuds"], extra_products["cable"], extra_products["glass"], extra_products["case"], extra_products["powerbank"]):
            for location, quantity in ((pos_location, 80), (cbd_pos, 55), (trm_pos, 45), (warehouse, 120)):
                self._ensure_opening_stock(organization, product, location, Decimal(quantity), inventory)

        self._seed_purchase_story(organization, suppliers, warehouse, all_products, inventory, manager, owner, today)
        self._seed_transfer_story(organization, warehouse, pos_location, cbd_pos, trm_pos, agent_location, all_products, manager, owner, agent, today)
        session_by_location = self._seed_sessions(organization, cashier, (pos_location, cbd_pos, trm_pos), now)
        sales = self._seed_sales_story(
            organization=organization,
            customers=customers,
            products=all_products,
            sessions=session_by_location,
            locations=(pos_location, cbd_pos, trm_pos),
            cashier=cashier,
            agent=agent,
            dsa=dsa,
            owner=owner,
            today=today,
        )
        self._seed_credit_returns_and_commissions(organization, sales, customers, cashier, agent, dsa, owner, today)
        self._seed_repair_story(organization, branch, repair_location, customers, all_products, technician, owner, today)
        self._seed_expense_story(organization, (branch, cbd_branch, trm_branch), manager, agent, owner, today)
        self._record_rich_activity(organization, owner, manager, cashier, agent, dsa, inventory, technician, today)

    def _location(self, organization, branch, code, name, location_type):
        location, _ = Location.objects.update_or_create(
            organization=organization,
            code=code,
            defaults={"branch": branch, "name": name, "location_type": location_type, "is_active": True},
        )
        return location

    def _product(self, organization, category, brand, sku, name, is_serialized, cost, price, image_url):
        product, _ = Product.objects.update_or_create(
            organization=organization,
            sku=sku,
            defaults={
                "category": category,
                "brand": brand,
                "name": name,
                "barcode": f"{organization.slug}-{sku}",
                "is_serialized": is_serialized,
                "image_url": image_url,
                "warranty_days": 365 if is_serialized else 90,
                "cost_price": cost,
                "selling_price": price,
                "tax_rate": Decimal("16.00"),
            },
        )
        return product

    def _seed_customers(self, organization):
        specs = [
            ("+254711100001", "Amina Mwikali", Decimal("0.00"), 0),
            ("+254711100002", "Peter Kamau", Decimal("30000.00"), 21),
            ("+254711100003", "Linet Wairimu", Decimal("45000.00"), 30),
            ("+254711100004", "Samuel Ochieng", Decimal("0.00"), 0),
            ("+254711100005", "Beatrice Naliaka", Decimal("60000.00"), 30),
            ("+254711100006", "Dennis Mutua", Decimal("15000.00"), 14),
            ("+254711100007", "Walk-in Customer", Decimal("0.00"), 0),
            ("+254711100008", "Corporate Staff Welfare", Decimal("120000.00"), 45),
        ]
        customers = []
        for phone, name, credit_limit, terms in specs:
            customer, _ = Contact.objects.update_or_create(
                organization=organization,
                phone_number=phone,
                defaults={
                    "contact_type": ContactType.CUSTOMER,
                    "name": name,
                    "email": f"{phone[-4:]}@customers.nairobi-mobile-hub.test",
                    "credit_limit": credit_limit,
                    "payment_terms_days": terms,
                },
            )
            customers.append(customer)
        return customers

    def _seed_suppliers(self, organization, base_supplier):
        specs = [
            (base_supplier.phone_number, base_supplier.name, base_supplier.email),
            ("+254722200001", "East Africa Device Distributors", "orders@eadd.test"),
            ("+254722200002", "Accessories Wholesale Kenya", "sales@awk.test"),
            ("+254722200003", "Refurbished iPhone Hub", "supply@iphub.test"),
        ]
        suppliers = []
        for phone, name, email in specs:
            supplier, _ = Contact.objects.update_or_create(
                organization=organization,
                phone_number=phone,
                defaults={"contact_type": ContactType.SUPPLIER, "name": name, "email": email},
            )
            suppliers.append(supplier)
        return suppliers

    def _ensure_opening_stock(self, organization, product, location, quantity, actor):
        if product.balances.filter(location=location).exists():
            return
        post_stock_movement(
            organization=organization,
            product=product,
            location=location,
            quantity=quantity,
            movement_type=StockMovementType.OPENING,
            actor=actor,
            unit_cost=product.cost_price,
            reason="Three-week demo opening stock",
        )

    def _seed_purchase_story(self, organization, suppliers, warehouse, products, inventory, manager, owner, today):
        purchase_specs = [
            ("RICH-PO-001", suppliers[1], PurchaseStatus.RECEIVED, today - timedelta(days=20), [("redmi", 18, 18), ("tecno", 14, 14)]),
            ("RICH-PO-002", suppliers[2], PurchaseStatus.RECEIVED, today - timedelta(days=17), [("cable", 150, 150), ("glass", 220, 220), ("case", 120, 120)]),
            ("RICH-PO-003", suppliers[3], PurchaseStatus.PART_RECEIVED, today - timedelta(days=13), [("iphone", 8, 6)]),
            ("RICH-PO-004", suppliers[0], PurchaseStatus.DISCREPANCY, today - timedelta(days=8), [("premium_phone", 5, 4), ("earbuds", 80, 76)]),
            ("RICH-PO-005", suppliers[2], PurchaseStatus.APPROVED, today - timedelta(days=2), [("powerbank", 60, 0), ("charger", 90, 0)]),
        ]
        for number, supplier, status, ordered_on, lines in purchase_specs:
            purchase, _ = PurchaseOrder.objects.update_or_create(
                organization=organization,
                number=number,
                defaults={
                    "supplier": supplier,
                    "destination": warehouse,
                    "status": status,
                    "ordered_on": ordered_on,
                    "supplier_reference": f"SUP-{number[-3:]}",
                    "notes": "Demo three-week purchasing scenario.",
                    "created_by": inventory,
                },
            )
            total = Decimal("0.00")
            first_line = None
            for product_key, quantity, received in lines:
                product = products[product_key]
                line, _ = PurchaseOrderLine.objects.update_or_create(
                    organization=organization,
                    order=purchase,
                    product=product,
                    defaults={
                        "quantity": Decimal(quantity),
                        "received_quantity": Decimal(received),
                        "unit_cost": product.cost_price,
                    },
                )
                first_line = first_line or line
                total += Decimal(received) * product.cost_price
            if total:
                Payable.objects.update_or_create(
                    organization=organization,
                    purchase_order=purchase,
                    defaults={
                        "supplier": supplier,
                        "original_amount": total,
                        "outstanding_amount": total if status != PurchaseStatus.RECEIVED else Decimal("0.00"),
                        "due_on": ordered_on + timedelta(days=14),
                        "is_settled": status == PurchaseStatus.RECEIVED,
                    },
                )
            if status == PurchaseStatus.DISCREPANCY and first_line:
                PurchaseDiscrepancy.objects.update_or_create(
                    organization=organization,
                    number="RICH-PD-001",
                    defaults={
                        "line": first_line,
                        "expected_quantity": first_line.quantity,
                        "accepted_quantity": first_line.received_quantity,
                        "damaged_quantity": Decimal("1"),
                        "missing_quantity": first_line.quantity - first_line.received_quantity,
                        "reason": "One device arrived with a cracked screen and one unit was short shipped.",
                        "status": PurchaseDiscrepancyStatus.PENDING,
                        "resolution": "Awaiting supplier credit note.",
                        "resolved_by": owner,
                    },
                )
                SupplierReturn.objects.update_or_create(
                    organization=organization,
                    number="RICH-SR-001",
                    defaults={
                        "line": first_line,
                        "quantity": Decimal("1"),
                        "reason": "Damaged phone returned to supplier for replacement.",
                        "status": SupplierReturnStatus.REQUESTED,
                        "requested_by": inventory,
                        "approved_by": manager,
                    },
                )

    def _seed_transfer_story(self, organization, warehouse, westlands, cbd, trm, agent_location, products, manager, owner, agent, today):
        transfer_specs = [
            ("RICH-TRF-001", westlands, TransferStatus.RECEIVED, "charger", 20, 20),
            ("RICH-TRF-002", cbd, TransferStatus.RECEIVED, "glass", 35, 35),
            ("RICH-TRF-003", trm, TransferStatus.RECEIVED, "case", 25, 25),
            ("RICH-TRF-004", agent_location, TransferStatus.RECEIVED, "cable", 20, 20),
            ("RICH-TRF-005", cbd, TransferStatus.IN_TRANSIT, "powerbank", 12, 0),
            ("RICH-TRF-006", trm, TransferStatus.DISCREPANCY, "earbuds", 15, 14),
        ]
        for offset, (number, destination, status, product_key, quantity, received) in enumerate(transfer_specs, start=1):
            transfer, _ = StockTransfer.objects.update_or_create(
                organization=organization,
                number=number,
                defaults={
                    "source": warehouse,
                    "destination": destination,
                    "status": status,
                    "requested_by": manager,
                    "approved_by": owner,
                    "notes": f"Demo branch allocation day {offset}.",
                },
            )
            line, _ = StockTransferLine.objects.update_or_create(
                organization=organization,
                transfer=transfer,
                product=products[product_key],
                defaults={"quantity": Decimal(quantity), "received_quantity": Decimal(received)},
            )
            if status == TransferStatus.DISCREPANCY:
                TransferDiscrepancy.objects.update_or_create(
                    organization=organization,
                    transfer=transfer,
                    line=line,
                    defaults={
                        "expected_quantity": Decimal(quantity),
                        "received_quantity": Decimal(received),
                        "difference": Decimal(received) - Decimal(quantity),
                        "reason": "One unit missing at receiving count.",
                        "status": DiscrepancyStatus.PENDING,
                        "resolution": "Branch manager to approve adjustment after CCTV review.",
                        "resolved_by": None,
                    },
                )

    def _seed_sessions(self, organization, cashier, locations, now):
        sessions = {}
        for index, location in enumerate(locations, start=1):
            session, _ = POSSession.objects.update_or_create(
                organization=organization,
                number=f"RICH-SESSION-{index:03d}",
                defaults={
                    "location": location,
                    "cashier": cashier,
                    "status": SessionStatus.REVIEWED,
                    "opening_float": Decimal("5000.00"),
                    "expected_cash": Decimal("42000.00") + Decimal(index * 5000),
                    "actual_cash": Decimal("42000.00") + Decimal(index * 5000),
                    "variance": Decimal("0.00"),
                    "closed_at": now - timedelta(days=index),
                    "closing_note": "Demo reviewed cashier session.",
                },
            )
            POSSession.objects.filter(pk=session.pk).update(opened_at=now - timedelta(days=21 - index))
            sessions[location.code] = session
        return sessions

    def _seed_sales_story(self, *, organization, customers, products, sessions, locations, cashier, agent, dsa, owner, today):
        serialized_products = [products["phone"], products["redmi"], products["tecno"], products["itel"], products["iphone"]]
        accessory_products = [products["charger"], products["earbuds"], products["cable"], products["glass"], products["case"], products["powerbank"]]
        serial_units = list(
            StockUnit.objects.filter(
                organization=organization,
                product__in=serialized_products,
                status=SerialStatus.AVAILABLE,
                location__in=locations,
            )
            .select_related("product", "location")
            .order_by("serial_number")
        )
        sales = []
        payment_methods = [PaymentMethod.CASH, PaymentMethod.MPESA, PaymentMethod.CARD, PaymentMethod.BANK]
        for index in range(1, 25):
            event_day = today - timedelta(days=22 - index)
            event_time = timezone.make_aware(datetime.combine(event_day, datetime.min.time())) + timedelta(hours=9 + (index % 8), minutes=index)
            location = locations[index % len(locations)]
            session = sessions[location.code]
            customer = customers[index % len(customers)]
            selling_serialized = bool(serial_units and index % 3 != 0)
            if selling_serialized:
                stock_unit = serial_units.pop(0)
                product = stock_unit.product
                quantity = Decimal("1")
            else:
                stock_unit = None
                product = accessory_products[index % len(accessory_products)]
                quantity = Decimal(1 + (index % 3))
            amounts = calculate_sale_line_amounts(product=product, quantity=quantity)
            is_credit = index in (6, 13, 19)
            paid_total = Decimal("3000.00") if is_credit else amounts["total"]
            status = SaleStatus.PART_PAID if is_credit else SaleStatus.PAID
            sale, _ = Sale.objects.update_or_create(
                organization=organization,
                number=f"RICH-SALE-{index:03d}",
                defaults={
                    "session": session,
                    "location": location,
                    "customer": customer,
                    "agent": dsa if index % 5 == 0 else agent if index % 4 == 0 else None,
                    "status": status,
                    "subtotal": amounts["gross"],
                    "tax_total": amounts["tax"],
                    "total": amounts["total"],
                    "paid_total": paid_total,
                    "created_by": cashier,
                    "completed_at": event_time,
                    "sale_channel": "credit" if is_credit else "cash",
                    "customer_national_id": "23456789" if is_credit else "",
                    "next_of_kin_name": "Credit Guarantor" if is_credit else "",
                    "next_of_kin_phone": "+254711900001" if is_credit else "",
                    "due_on": event_day + timedelta(days=14) if is_credit else None,
                },
            )
            line, _ = SaleLine.objects.update_or_create(
                organization=organization,
                sale=sale,
                product=product,
                stock_unit=stock_unit,
                defaults={
                    "quantity": quantity,
                    "unit_price": product.selling_price,
                    "unit_cost": product.cost_price,
                    "tax": amounts["tax"],
                    "line_total": amounts["gross"],
                },
            )
            payment_method = PaymentMethod.CREDIT if is_credit else payment_methods[index % len(payment_methods)]
            Payment.objects.update_or_create(
                organization=organization,
                number=f"RICH-PAY-{index:03d}",
                defaults={
                    "customer": customer,
                    "sale": sale,
                    "method": payment_method,
                    "status": PaymentStatus.PENDING if is_credit else PaymentStatus.CONFIRMED,
                    "amount": sale.total - sale.paid_total if is_credit else sale.total,
                    "provider_reference": f"MPESA-RICH-{index:03d}" if payment_method == PaymentMethod.MPESA else "",
                    "received_by": cashier,
                },
            )
            Sale.objects.filter(pk=sale.pk).update(created_at=event_time, completed_at=event_time)
            SaleLine.objects.filter(pk=line.pk).update(created_at=event_time)
            Payment.objects.filter(organization=organization, number=f"RICH-PAY-{index:03d}").update(created_at=event_time, received_at=event_time)
            if stock_unit:
                if not stock_unit.movements.filter(reference_type="sale", reference_id=sale.number).exists():
                    post_stock_movement(
                        organization=organization,
                        product=product,
                        location=stock_unit.location,
                        quantity=-1,
                        movement_type=StockMovementType.SALE,
                        actor=cashier,
                        stock_unit=stock_unit,
                        unit_cost=product.cost_price,
                        reference_type="sale",
                        reference_id=sale.number,
                        reason="Demo serialized retail sale",
                    )
                stock_unit.status = SerialStatus.SOLD
                stock_unit.location = location
                stock_unit.save(update_fields=["status", "location", "updated_at"])
            elif not product.stock_movements.filter(reference_type="sale", reference_id=sale.number).exists():
                post_stock_movement(
                    organization=organization,
                    product=product,
                    location=location,
                    quantity=-quantity,
                    movement_type=StockMovementType.SALE,
                    actor=cashier,
                    unit_cost=product.cost_price,
                    reference_type="sale",
                    reference_id=sale.number,
                    reason="Demo accessory retail sale",
                )
            sales.append(sale)
        return sales

    def _seed_credit_returns_and_commissions(self, organization, sales, customers, cashier, agent, dsa, owner, today):
        rule, _ = CommissionRule.objects.update_or_create(
            organization=organization,
            name="Three-week agent commission",
            defaults={"percentage": Decimal("2.00"), "requires_full_payment": True, "is_active": True},
        )
        payable_accruals = []
        for sale in sales:
            if sale.agent_id:
                amount = (sale.total * Decimal("0.02")).quantize(Decimal("0.01"))
                accrual, _ = CommissionAccrual.objects.update_or_create(
                    organization=organization,
                    sale=sale,
                    agent=sale.agent,
                    rule=rule,
                    defaults={"amount": amount, "is_payable": sale.status == SaleStatus.PAID},
                )
                if accrual.is_payable:
                    payable_accruals.append(accrual)
            if sale.status == SaleStatus.PART_PAID and sale.customer:
                receivable, _ = Receivable.objects.update_or_create(
                    organization=organization,
                    sale=sale,
                    defaults={
                        "customer": sale.customer,
                        "original_amount": sale.total,
                        "outstanding_amount": sale.balance_due,
                        "due_on": sale.due_on or today + timedelta(days=14),
                    },
                )
                installment_amount = (sale.balance_due / Decimal("2")).quantize(Decimal("0.01"))
                for sequence in (1, 2):
                    ReceivableInstallment.objects.update_or_create(
                        organization=organization,
                        receivable=receivable,
                        sequence=sequence,
                        schedule_version=1,
                        defaults={
                            "due_on": receivable.due_on + timedelta(days=(sequence - 1) * 14),
                            "amount": installment_amount,
                            "paid_amount": Decimal("0.00"),
                            "status": InstallmentStatus.OVERDUE if sequence == 1 and receivable.due_on < today else InstallmentStatus.PENDING,
                            "is_current": True,
                        },
                    )
        if payable_accruals:
            payout_amount = sum((accrual.amount for accrual in payable_accruals[:4]), Decimal("0.00"))
            payout, _ = CommissionPayout.objects.update_or_create(
                organization=organization,
                number="RICH-COMM-PAYOUT-001",
                defaults={
                    "agent": payable_accruals[0].agent,
                    "period_start": today - timedelta(days=21),
                    "period_end": today - timedelta(days=7),
                    "amount": payout_amount,
                    "status": CommissionPayoutStatus.PAID,
                    "requested_by": payable_accruals[0].agent,
                    "approved_by": owner,
                    "paid_at": timezone.now() - timedelta(days=2),
                    "payment_reference": "MPESA-COMM-001",
                },
            )
            for accrual in payable_accruals[:4]:
                CommissionPayoutLine.objects.update_or_create(
                    organization=organization,
                    accrual=accrual,
                    defaults={"payout": payout, "amount": accrual.amount},
                )
                CommissionAccrual.objects.filter(pk=accrual.pk).update(paid_at=payout.paid_at)

        returned_sale = sales[4]
        line = returned_sale.lines.first()
        if line:
            sale_return, _ = SaleReturn.objects.update_or_create(
                organization=organization,
                number="RICH-RET-001",
                defaults={
                    "sale": returned_sale,
                    "status": ReturnStatus.COMPLETED,
                    "reason": "Customer returned accessory within same week due wrong cable type.",
                    "refund_amount": line.total_after_tax,
                    "outcome": ReturnOutcome.REFUND,
                    "requested_by": cashier,
                    "approved_by": owner,
                },
            )
            SaleReturnLine.objects.update_or_create(
                organization=organization,
                sale_return=sale_return,
                sale_line=line,
                defaults={
                    "quantity": line.quantity,
                    "disposition": ReturnDisposition.RESTOCK,
                    "refundable_amount": line.total_after_tax,
                },
            )
            Sale.objects.filter(pk=returned_sale.pk).update(status=SaleStatus.RETURNED)
            SaleLine.objects.filter(pk=line.pk).update(returned_quantity=line.quantity)
            payment = returned_sale.payments.first()
            if payment:
                Refund.objects.update_or_create(
                    organization=organization,
                    number="RICH-REF-001",
                    defaults={
                        "payment": payment,
                        "amount": line.total_after_tax,
                        "reason": "Approved demo return refund.",
                        "status": PaymentStatus.CONFIRMED,
                        "approved_by": owner,
                    },
                )

    def _seed_repair_story(self, organization, branch, repair_location, customers, products, technician, owner, today):
        statuses = [
            RepairStatus.RECEIVED,
            RepairStatus.DIAGNOSING,
            RepairStatus.AWAITING_APPROVAL,
            RepairStatus.IN_REPAIR,
            RepairStatus.READY,
            RepairStatus.CLOSED,
        ]
        for index, status in enumerate(statuses, start=1):
            ticket, _ = RepairTicket.objects.update_or_create(
                organization=organization,
                number=f"RICH-REP-{index:03d}",
                defaults={
                    "branch": branch,
                    "customer": customers[index % len(customers)],
                    "status": status,
                    "issue": [
                        "Screen cracked after drop.",
                        "Charging port intermittent.",
                        "Battery drains quickly.",
                        "Speaker not clear during calls.",
                        "Warranty screen protector replacement.",
                        "Completed board cleaning after liquid exposure.",
                    ][index - 1],
                    "diagnosis": "<p>Demo diagnosis captured with technician notes.</p>",
                    "technician": technician,
                    "warranty": index in (5,),
                    "warranty_type": "customer" if index == 5 else "none",
                    "warranty_decision_notes": "Covered under accessory warranty." if index == 5 else "",
                    "quoted_amount": Decimal("1500.00") + Decimal(index * 350),
                    "collected_at": timezone.now() - timedelta(days=1) if status == RepairStatus.CLOSED else None,
                },
            )
            if index in (4, 6):
                RepairPartUsage.objects.update_or_create(
                    organization=organization,
                    ticket=ticket,
                    product=products["glass"] if index == 4 else products["cable"],
                    location=repair_location,
                    defaults={
                        "quantity": Decimal("1"),
                        "unit_cost": products["glass"].cost_price if index == 4 else products["cable"].cost_price,
                        "used_by": technician,
                    },
                )
            RepairTicket.objects.filter(pk=ticket.pk).update(created_at=timezone.now() - timedelta(days=8 - index))

    def _seed_expense_story(self, organization, branches, manager, agent, owner, today):
        expenses = [
            ("RICH-EXP-001", branches[0], "Rent", "Westlands branch monthly rent", "85000.00", ExpenseStatus.APPROVED, manager),
            ("RICH-EXP-002", branches[1], "Transport", "CBD stock delivery rider", "2400.00", ExpenseStatus.APPROVED, manager),
            ("RICH-EXP-003", branches[2], "Marketing", "TRM opening weekend posters", "7200.00", ExpenseStatus.SUBMITTED, manager),
            ("RICH-EXP-004", branches[0], "Agent Field Work", "Agent customer visit transport", "1800.00", ExpenseStatus.SUBMITTED, agent),
            ("RICH-EXP-005", branches[0], "Utilities", "Internet and till line bundle", "5500.00", ExpenseStatus.APPROVED, manager),
        ]
        for offset, (number, expense_branch, category, description, amount, status, requester) in enumerate(expenses, start=1):
            Expense.objects.update_or_create(
                organization=organization,
                number=number,
                defaults={
                    "branch": expense_branch,
                    "category": category,
                    "description": description,
                    "amount": Decimal(amount),
                    "status": status,
                    "incurred_on": today - timedelta(days=offset * 3),
                    "requested_by": requester,
                    "approved_by": owner if status == ExpenseStatus.APPROVED else None,
                },
            )

    def _record_rich_activity(self, organization, owner, manager, cashier, agent, dsa, inventory, technician, today):
        event_specs = [
            ("auth.login", owner, "Owner reviewed all-branch performance dashboard."),
            ("auth.login", owner, "Owner checked exception report and overdue receivables."),
            ("auth.login", owner, "Owner approved commission payout."),
            ("inventory.batch_intake", inventory, "Inventory officer uploaded supplier IMEI batch."),
            ("inventory.transfer_completed", manager, "Manager completed branch transfer receipt."),
            ("inventory.transfer_discrepancy", manager, "Manager logged transfer shortage requiring approval."),
            ("sales.completed", cashier, "Cashier completed card, cash, bank, and M-Pesa demo sales."),
            ("sales.credit_created", cashier, "Cashier created customer credit sale with installment schedule."),
            ("sales.return_refunded", cashier, "Cashier processed approved customer return and refund."),
            ("agents.stock_allocated", agent, "Agent received accessories for field selling."),
            ("agents.dsa_sale", dsa, "DSA completed assisted device sale."),
            ("repairs.ticket_updated", technician, "Technician updated repair diagnosis and part usage."),
            ("purchasing.discrepancy_logged", inventory, "Inventory officer logged supplier delivery discrepancy."),
            ("reports.reviewed", owner, "Owner reviewed IMEI history and agent network report."),
        ]
        for offset, (action, actor, message) in enumerate(event_specs):
            if organization.auditevent_set.filter(action=action, actor=actor, message=message).exists():
                continue
            event = record_audit_event(action=action, actor=actor, organization=organization, target=organization, message=message)
            event_time = timezone.now() - timedelta(days=21 - offset)
            organization.auditevent_set.filter(pk=event.pk).update(created_at=event_time)

    def _record_demo_activity(self, organization, owner, manager, cashier, agent, dsa, agent_profile, dsa_profile):
        events = [
            ("auth.login", owner, "Owner logged in to review executive dashboard.", owner),
            ("auth.login", manager, "Branch manager logged in to review pending transfer.", manager),
            ("auth.login", cashier, "Cashier opened POS session and completed demo sales.", cashier),
            ("inventory.transfer_requested", manager, "Manager requested warehouse to POS transfer.", None),
            ("inventory.agent_allocated", agent, "Agent received serialized stock allocation.", agent_profile),
            ("agents.dsa_registered", agent, "Agent registered and linked a Direct Sales Agent.", dsa_profile),
            ("sales.completed", cashier, "Cashier completed demo cash and M-Pesa sales.", None),
            ("reports.reviewed", owner, "Owner reviewed activity and operational reports.", organization),
            ("auth.login", dsa, "DSA logged in to review assigned sales workflow.", dsa),
        ]
        for action, actor, message, target in events:
            if organization.auditevent_set.filter(action=action, actor=actor, message=message).exists():
                continue
            record_audit_event(
                action=action,
                actor=actor,
                organization=organization,
                target=target or organization,
                message=message,
            )

    def _upsert_user(self, *, username, email, password, **defaults):
        user, _ = User.objects.update_or_create(
            username=username,
            defaults={"email": email, **defaults},
        )
        user.set_password(password)
        user.save(update_fields=["password", "email", *defaults.keys()])
        return user
