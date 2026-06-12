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
from apps.repairs.models import RepairStatus, RepairTicket
from apps.sales.models import Sale, SaleLine, SaleStatus
from apps.organizations.models import (
    Announcement,
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
            "name": "Kipekee Electronics",
            "slug": "kipekee-electronics",
            "email": "hello@kipekee-electronics.test",
            "phone_number": "+254700000101",
        },
        "owner": {
            "username": "alice",
            "email": "alice@kipekee-electronics.test",
            "first_name": "Alice",
            "last_name": "Wanjiku",
            "phone_number": "+254700000111",
        },
        "company": {
            "name": "Kipekee Electronics Limited",
            "code": "KEL",
            "legal_name": "Kipekee Electronics Limited",
            "tax_number": "P051234567A",
        },
        "branch": {
            "name": "Nairobi CBD Branch",
            "code": "NBO-CBD",
            "email": "cbd@kipekee-electronics.test",
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
            email="admin@kipekee-access.test",
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
            title="Welcome to Kipekee Access",
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
        phones, _ = Category.objects.update_or_create(
            organization=organization, code="phones", defaults={"name": "Phones", "is_active": True}
        )
        accessories, _ = Category.objects.update_or_create(
            organization=organization, code="accessories", defaults={"name": "Accessories", "is_active": True}
        )
        brand, _ = Brand.objects.update_or_create(
            organization=organization, name="Kipekee Mobile", defaults={"is_active": True}
        )
        phone, _ = Product.objects.update_or_create(
            organization=organization, sku="KM-A07-64",
            defaults={
                "category": phones, "brand": brand, "name": "A07 64GB/4GB",
                "barcode": f"{organization.slug}-PHONE", "is_serialized": True,
                "warranty_days": 365, "cost_price": Decimal("15000.00"),
                "selling_price": Decimal("18600.00"), "tax_rate": Decimal("16.00"),
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
        customer, _ = Contact.objects.update_or_create(
            organization=organization, phone_number=f"{organization.phone_number}9",
            defaults={
                "contact_type": ContactType.CUSTOMER, "name": "Demo Credit Customer",
                "email": f"customer@{organization.slug}.test", "credit_limit": Decimal("50000.00"),
                "payment_terms_days": 30,
            },
        )
        Contact.objects.update_or_create(
            organization=organization, phone_number=f"{organization.phone_number}8",
            defaults={
                "contact_type": ContactType.SUPPLIER, "name": "Demo Device Supplier",
                "email": f"supplier@{organization.slug}.test",
            },
        )
        serial = f"{str(organization.id).replace('-', '')[:8]}000000001"
        stock_unit, created = StockUnit.objects.get_or_create(
            organization=organization, serial_number=serial,
            defaults={
                "product": phone, "location": pos_location, "status": SerialStatus.AVAILABLE,
                "unit_cost": phone.cost_price,
            },
        )
        if created:
            post_stock_movement(
                organization=organization, product=phone, location=pos_location, quantity=1,
                movement_type=StockMovementType.OPENING, actor=owner, stock_unit=stock_unit,
                unit_cost=phone.cost_price, reason="Demo opening stock",
            )
        if not charger.balances.filter(location=pos_location).exists():
            post_stock_movement(
                organization=organization, product=charger, location=pos_location, quantity=25,
                movement_type=StockMovementType.OPENING, actor=owner, unit_cost=charger.cost_price,
                reason="Demo opening stock",
            )
        session, _ = POSSession.objects.update_or_create(
            organization=organization, number="DEMO-SESSION-001",
            defaults={
                "location": pos_location, "cashier": owner, "status": SessionStatus.OPEN,
                "opening_float": Decimal("5000.00"), "expected_cash": Decimal("1500.00"),
            },
        )
        sale, _ = Sale.objects.update_or_create(
            organization=organization, number="DEMO-SALE-001",
            defaults={
                "session": session, "location": pos_location, "customer": customer, "agent": owner,
                "status": SaleStatus.PAID, "subtotal": Decimal("1500.00"), "total": Decimal("1500.00"),
                "paid_total": Decimal("1500.00"), "created_by": owner, "completed_at": timezone.now(),
            },
        )
        SaleLine.objects.update_or_create(
            organization=organization, sale=sale, product=charger,
            defaults={
                "quantity": 1, "unit_price": charger.selling_price, "unit_cost": charger.cost_price,
                "line_total": charger.selling_price,
            },
        )
        Payment.objects.update_or_create(
            organization=organization, number="DEMO-PAY-001",
            defaults={
                "customer": customer, "sale": sale, "method": PaymentMethod.MPESA,
                "status": PaymentStatus.CONFIRMED, "amount": sale.total,
                "provider_reference": f"DEMO{str(organization.id)[:6].upper()}", "received_by": owner,
            },
        )
        rule, _ = CommissionRule.objects.update_or_create(
            organization=organization, name="Default sales commission",
            defaults={"percentage": Decimal("2.50"), "requires_full_payment": True, "is_active": True},
        )
        CommissionAccrual.objects.update_or_create(
            organization=organization, sale=sale, agent=owner, rule=rule,
            defaults={"amount": Decimal("37.50"), "is_payable": True},
        )
        Expense.objects.update_or_create(
            organization=organization, number="DEMO-EXP-001",
            defaults={
                "branch": branch, "category": "Utilities", "description": "Demo internet expense",
                "amount": Decimal("3500.00"), "status": ExpenseStatus.APPROVED,
                "incurred_on": timezone.localdate(), "requested_by": owner, "approved_by": owner,
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
        queue_integration_event(
            organization=organization, provider="etims", event_type="invoice.submit",
            idempotency_key=f"demo-etims-{organization.id}", payload={"sale_number": sale.number},
        )

    def _upsert_user(self, *, username, email, password, **defaults):
        user, _ = User.objects.update_or_create(
            username=username,
            defaults={"email": email, **defaults},
        )
        user.set_password(password)
        user.save(update_fields=["password", "email", *defaults.keys()])
        return user
