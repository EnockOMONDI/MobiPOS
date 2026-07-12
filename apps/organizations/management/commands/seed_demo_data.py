from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import Permission
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.audit.services import record_audit_event
from apps.catalog.models import Brand, Category, Product
from apps.commissions.models import CommissionAccrual, CommissionRule
from apps.contacts.models import Contact, ContactType
from apps.expenses.models import Expense, ExpenseStatus
from apps.integrations.services import queue_integration_event
from apps.inventory.models import SerialStatus, StockMovementType, StockUnit
from apps.inventory.services import post_stock_movement
from apps.notifications.models import Notification
from apps.payments.models import Payment, PaymentMethod, PaymentStatus
from apps.pos.models import POSSession, SessionStatus
from apps.purchasing.models import PurchaseOrder, PurchaseOrderLine, PurchaseStatus
from apps.repairs.models import RepairStatus, RepairTicket
from apps.sales.models import Sale, SaleLine, SaleStatus
from apps.sales.services import calculate_sale_line_amounts
from apps.transfers.models import StockTransfer, StockTransferLine, TransferStatus
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
            "name": "MobiPOS Electronics",
            "slug": "mobipos-electronics",
            "email": "hello@mobipos-electronics.test",
            "phone_number": "+254700000101",
        },
        "owner": {
            "username": "alice",
            "email": "alice@mobipos-electronics.test",
            "first_name": "Alice",
            "last_name": "Wanjiku",
            "phone_number": "+254700000111",
        },
        "company": {
            "name": "MobiPOS Electronics Limited",
            "code": "MEL",
            "legal_name": "MobiPOS Electronics Limited",
            "tax_number": "P051234567A",
        },
        "branch": {
            "name": "Nairobi CBD Branch",
            "code": "NBO-CBD",
            "email": "cbd@mobipos-electronics.test",
            "phone_number": "+254700000121",
        },
    },
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
        )

        for dataset in DEMO_ORGANIZATIONS:
            self._seed_organization(dataset, plan, admin)

        self.stdout.write(self.style.SUCCESS("Demo data is ready."))
        self.stdout.write("")
        self.stdout.write("Platform admin: platformadmin / AdminPass123!")
        self.stdout.write("Organization owner 1: alice / DemoPass123!")
        self.stdout.write("Organization owner 2: brian / DemoPass123!")
        self.stdout.write("Extra demo staff per organization use DemoPass123!: manager, cashier, agent, dsa")

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
                "warranty_days": 365, "cost_price": Decimal("15000.00"),
                "selling_price": Decimal("18600.00"), "tax_rate": Decimal("16.00"),
            },
        )
        premium_phone, _ = Product.objects.update_or_create(
            organization=organization, sku="MP-S24-256",
            defaults={
                "category": phones, "brand": brand, "name": "S24 Ultra 256GB",
                "barcode": f"{organization.slug}-S24", "is_serialized": True,
                "warranty_days": 365, "cost_price": Decimal("118000.00"),
                "selling_price": Decimal("139500.00"), "tax_rate": Decimal("16.00"),
            },
        )
        charger, _ = Product.objects.update_or_create(
            organization=organization, sku="CHG-20W",
            defaults={
                "category": accessories, "brand": brand, "name": "20W Fast Charger",
                "barcode": f"{organization.slug}-CHARGER", "is_serialized": False,
                "warranty_days": 90, "cost_price": Decimal("850.00"),
                "selling_price": Decimal("1500.00"), "tax_rate": Decimal("16.00"),
            },
        )
        earbuds, _ = Product.objects.update_or_create(
            organization=organization, sku="EAR-PRO",
            defaults={
                "category": accessories, "brand": brand, "name": "Wireless Earbuds Pro",
                "barcode": f"{organization.slug}-EARBUDS", "is_serialized": False,
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
            else:
                role.permissions.set(permissions.filter(content_type__app_label__in=("sales", "notifications")))
            roles[key] = role

        user_specs = {
            "manager": ("manager", "Mary", "Mwangi", "+254700100001"),
            "cashier": ("cashier", "Kevin", "Otieno", "+254700100002"),
            "agent": ("agent", "Renny", "Kiptoo", "+254700100003"),
            "dsa": ("dsa", "Faith", "Achieng", "+254700100004"),
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
