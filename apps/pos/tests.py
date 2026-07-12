import pytest
from django.contrib.auth.models import Permission
from django.db import IntegrityError
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Category, Product
from apps.inventory.models import SerialStatus, StockMovementType, StockUnit
from apps.inventory.services import post_stock_movement
from apps.organizations.models import Branch, Company, Location, Membership, MembershipStatus, Organization, Role
from apps.sales.models import Sale
from apps.payments.models import Payment, PaymentStatus
from apps.contacts.models import Contact
from apps.operations.models import Receivable
from apps.operations.models import ApprovalRequest
from apps.pos.models import CashMovement, POSSession


def grant_sale_permission(membership):
    permission = Permission.objects.get(content_type__app_label="sales", codename="add_sale")
    role = Role.objects.create(organization=membership.organization, name="Cashier", code=f"cashier-{membership.user.username}")
    role.permissions.add(permission)
    membership.roles.add(role)


@pytest.mark.django_db
def test_duplicate_open_session_for_same_cashier_location_is_rejected():
    user = User.objects.create_user(username="session-unique", email="session-unique@example.com")
    org = Organization.objects.create(name="Session Unique", slug="session-unique", status="active")
    company = Company.objects.create(organization=org, name="Company", code="CO")
    branch = Branch.objects.create(organization=org, company=company, name="Main", code="MAIN")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="POS", location_type="pos")
    POSSession.objects.create(organization=org, number="S1", location=location, cashier=user)

    with pytest.raises(IntegrityError):
        POSSession.objects.create(organization=org, number="S2", location=location, cashier=user)


@pytest.mark.django_db
def test_duplicate_active_cart_for_same_session_user_is_rejected():
    user = User.objects.create_user(username="cart-unique", email="cart-unique@example.com")
    org = Organization.objects.create(name="Cart Unique", slug="cart-unique", status="active")
    company = Company.objects.create(organization=org, name="Company", code="CO")
    branch = Branch.objects.create(organization=org, company=company, name="Main", code="MAIN")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="POS", location_type="pos")
    session = POSSession.objects.create(organization=org, number="S1", location=location, cashier=user)
    Sale.objects.create(organization=org, number="CART1", session=session, location=location, created_by=user)

    with pytest.raises(IntegrityError):
        Sale.objects.create(organization=org, number="CART2", session=session, location=location, created_by=user)


@pytest.mark.django_db
def test_unified_checkout_completes_quantity_sale(client):
    user = User.objects.create_user(username="cashier", email="checkout@example.com")
    org = Organization.objects.create(name="Retail", slug="checkout-retail", status="active")
    company = Company.objects.create(organization=org, name="Retail Ltd", code="RET")
    branch = Branch.objects.create(organization=org, company=company, name="Main", code="MAIN")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="POS", location_type="pos")
    membership = Membership.objects.create(
        organization=org, user=user, status=MembershipStatus.ACTIVE, is_owner=True,
    )
    membership.branches.add(branch)
    grant_sale_permission(membership)
    category = Category.objects.create(organization=org, name="Accessories", code="accessories")
    product = Product.objects.create(organization=org, category=category, name="Charger", sku="CHG", selling_price=1500, cost_price=800)
    post_stock_movement(organization=org, product=product, location=location, quantity=5, movement_type=StockMovementType.OPENING, actor=user)
    client.force_login(user)

    response = client.post(reverse("pos-checkout"), {"product": product.id, "quantity": 1, "payment_method": "cash", "amount_received": 1500})

    assert response.status_code == 302
    assert Sale.objects.get(organization=org).status == "paid"


@pytest.mark.django_db
def test_unified_checkout_calculates_product_tax(client):
    user = User.objects.create_user(username="taxcashier", email="tax@example.com")
    org = Organization.objects.create(name="Tax Retail", slug="tax-retail", status="active")
    company = Company.objects.create(organization=org, name="Tax Ltd", code="TAX")
    branch = Branch.objects.create(organization=org, company=company, name="Main", code="TAXMAIN")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="TAXPOS", location_type="pos")
    membership = Membership.objects.create(
        organization=org, user=user, status=MembershipStatus.ACTIVE, is_owner=True,
    )
    membership.branches.add(branch)
    grant_sale_permission(membership)
    category = Category.objects.create(organization=org, name="Accessories", code="tax-accessories")
    product = Product.objects.create(
        organization=org, category=category, name="Taxable Charger", sku="TAX-CHG",
        selling_price=1000, cost_price=500, tax_rate=16,
    )
    post_stock_movement(
        organization=org, product=product, location=location, quantity=5,
        movement_type=StockMovementType.OPENING, actor=user,
    )
    client.force_login(user)

    response = client.post(reverse("pos-checkout"), {
        "product": product.id, "quantity": 1, "payment_method": "cash", "amount_received": 1160,
    })

    sale = Sale.objects.get(organization=org)
    assert response.status_code == 302
    assert sale.subtotal == 1000
    assert sale.tax_total == 160
    assert sale.total == 1160
    assert sale.paid_total == 1160


@pytest.mark.django_db
def test_checkout_creates_controlled_credit_receivable(client):
    user = User.objects.create_user(username="creditcashier", email="credit@example.com")
    org = Organization.objects.create(name="Credit Retail", slug="credit-retail", status="active")
    company = Company.objects.create(organization=org, name="Credit Ltd", code="CRED")
    branch = Branch.objects.create(organization=org, company=company, name="Main", code="CMAIN")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="CPOS", location_type="pos")
    membership = Membership.objects.create(
        organization=org, user=user, status=MembershipStatus.ACTIVE, is_owner=True,
    )
    membership.branches.add(branch)
    grant_sale_permission(membership)
    category = Category.objects.create(organization=org, name="Accessories", code="credit-accessories")
    product = Product.objects.create(organization=org, category=category, name="Charger", sku="CCHG", selling_price=1500, cost_price=800)
    customer = Contact.objects.create(organization=org, contact_type="customer", name="Credit Customer", credit_limit=2000, payment_terms_days=30)
    post_stock_movement(organization=org, product=product, location=location, quantity=5, movement_type=StockMovementType.OPENING, actor=user)
    client.force_login(user)

    response = client.post(reverse("pos-checkout"), {"product": product.id, "customer": customer.id, "quantity": 1, "payment_method": "cash", "amount_received": 500})

    assert response.status_code == 302
    receivable = Receivable.objects.get(organization=org)
    assert receivable.outstanding_amount == 1000
    assert receivable.sale.paid_total == 500
    assert receivable.installments.get().amount == 1000


@pytest.mark.django_db
def test_cart_credit_sale_requires_owner_approval(client):
    cashier = User.objects.create_user(username="creditcart", email="creditcart@example.com")
    owner = User.objects.create_user(username="creditowner", email="creditowner@example.com")
    org = Organization.objects.create(name="Approval Retail", slug="approval-retail", status="active")
    company = Company.objects.create(organization=org, name="Approval Ltd", code="APR")
    branch = Branch.objects.create(organization=org, company=company, name="Main", code="APRMAIN")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="APRPOS", location_type="pos")
    membership = Membership.objects.create(organization=org, user=cashier, status=MembershipStatus.ACTIVE)
    membership.branches.add(branch)
    grant_sale_permission(membership)
    Membership.objects.create(organization=org, user=owner, status=MembershipStatus.ACTIVE, is_owner=True)
    category = Category.objects.create(organization=org, name="Accessories", code="approval-accessories")
    product = Product.objects.create(
        organization=org, category=category, name="Credit Phone", sku="APR-PHONE", selling_price=2000,
    )
    customer = Contact.objects.create(
        organization=org, contact_type="customer", name="Approved Customer",
        credit_limit=5000, payment_terms_days=30,
    )
    post_stock_movement(
        organization=org, product=product, location=location, quantity=5,
        movement_type=StockMovementType.OPENING, actor=cashier,
    )
    client.force_login(cashier)
    client.post(reverse("pos-cart-add"), {"product": product.id, "quantity": "1"})
    cart = Sale.objects.get(organization=org, status="draft")

    client.post(reverse("pos-cart-complete", args=[cart.id]), {
        "customer": customer.id, "cash_amount": "500",
    })
    cart.refresh_from_db()
    approval = ApprovalRequest.objects.get(target_id=str(cart.id), request_type="credit_sale")
    assert cart.status == "draft"

    client.force_login(owner)
    client.post(reverse("approval-decide", args=[approval.id]), {"decision": "approved"})
    client.force_login(cashier)
    response = client.post(reverse("pos-cart-complete", args=[cart.id]), {
        "customer": customer.id, "cash_amount": "500",
    })

    cart.refresh_from_db()
    assert response.status_code == 302
    assert cart.status == "part_paid"
    assert cart.receivable.outstanding_amount == 1500


@pytest.mark.django_db
def test_cashier_can_close_session(client):
    user = User.objects.create_user(username="closer", email="closer@example.com")
    org = Organization.objects.create(name="Close Retail", slug="close-retail", status="active")
    company = Company.objects.create(organization=org, name="Close Ltd", code="CLOSE")
    branch = Branch.objects.create(organization=org, company=company, name="Main", code="CLMAIN")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="CLPOS", location_type="pos")
    membership = Membership.objects.create(organization=org, user=user, status=MembershipStatus.ACTIVE)
    membership.branches.add(branch)
    session = POSSession.objects.create(organization=org, number="SESSION-CLOSE", location=location, cashier=user, opening_float=1000)
    client.force_login(user)

    response = client.post(reverse("session-close", args=[session.id]), {"actual_cash": "1000", "closing_note": "Balanced"})

    session.refresh_from_db()
    assert response.status_code == 302
    assert session.status == "closed"
    assert session.variance == 0


@pytest.mark.django_db
def test_explicit_register_open_and_cash_movements_feed_reconciliation(client, settings):
    settings.POS_AUTO_OPEN_SESSION = False
    user = User.objects.create_user(username="register", email="register@example.com")
    org = Organization.objects.create(name="Register Retail", slug="register-retail", status="active")
    company = Company.objects.create(organization=org, name="Register Ltd", code="REGISTER")
    branch = Branch.objects.create(organization=org, company=company, name="Main", code="REGMAIN")
    Location.objects.create(organization=org, branch=branch, name="POS", code="REGPOS", location_type="pos")
    membership = Membership.objects.create(organization=org, user=user, status=MembershipStatus.ACTIVE)
    membership.branches.add(branch)
    grant_sale_permission(membership)
    client.force_login(user)

    assert client.get(reverse("pos-cart")).status_code == 302
    response = client.post(reverse("session-open"), {"opening_float": "1000"})
    session = POSSession.objects.get(organization=org)
    client.post(reverse("cash-movement-create", args=[session.id]), {
        "movement_type": "cash_in", "amount": "200", "reason": "Change float",
    })
    client.post(reverse("cash-movement-create", args=[session.id]), {
        "movement_type": "drop", "amount": "300", "reason": "Safe drop",
    })
    client.post(reverse("session-close", args=[session.id]), {"actual_cash": "900", "closing_note": "Balanced"})

    session.refresh_from_db()
    assert response.status_code == 302
    assert CashMovement.objects.filter(session=session).count() == 2
    assert session.expected_cash == 900
    assert session.variance == 0


@pytest.mark.django_db
def test_owner_can_review_closed_cashier_session(client):
    from django.core.management import call_command
    call_command("seed_demo_data")
    owner = User.objects.get(username="alice")
    org = Organization.objects.get(slug="mobipos-electronics")
    session = POSSession.objects.get(organization=org)
    session.status = "closed"
    session.save(update_fields=["status", "updated_at"])
    client.force_login(owner)

    response = client.post(reverse("session-review", args=[session.id]))

    session.refresh_from_db()
    assert response.status_code == 302
    assert session.status == "reviewed"


@pytest.mark.django_db
def test_multi_line_cart_completes_one_sale(client):
    user = User.objects.create_user(username="cartcashier", email="cart@example.com")
    org = Organization.objects.create(name="Cart Retail", slug="cart-retail", status="active")
    company = Company.objects.create(organization=org, name="Cart Ltd", code="CART")
    branch = Branch.objects.create(organization=org, company=company, name="Main", code="CARTMAIN")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="CARTPOS", location_type="pos")
    membership = Membership.objects.create(organization=org, user=user, status=MembershipStatus.ACTIVE)
    membership.branches.add(branch)
    grant_sale_permission(membership)
    category = Category.objects.create(organization=org, name="Accessories", code="cart-accessories")
    first = Product.objects.create(organization=org, category=category, name="Cable", sku="CABLE", selling_price=500)
    second = Product.objects.create(organization=org, category=category, name="Case", sku="CASE", selling_price=1000)
    for product in (first, second):
        post_stock_movement(
            organization=org, product=product, location=location, quantity=5,
            movement_type=StockMovementType.OPENING, actor=user,
        )
    client.force_login(user)

    client.post(reverse("pos-cart-add"), {"product": first.id, "quantity": "2"})
    client.post(reverse("pos-cart-add"), {"product": second.id, "quantity": "1"})
    cart = Sale.objects.get(organization=org, status="draft")
    response = client.post(reverse("pos-cart-complete", args=[cart.id]), {
        "payment_method": "cash", "amount_received": "2000",
    })

    cart.refresh_from_db()
    assert response.status_code == 302
    assert cart.status == "paid"
    assert cart.lines.count() == 2
    assert cart.total == 2000


@pytest.mark.django_db
def test_cart_scanner_lookup_adds_serialized_stock_unit(client):
    user = User.objects.create_user(username="scanner", email="scanner@example.com")
    org = Organization.objects.create(name="Scanner Retail", slug="scanner-retail", status="active")
    company = Company.objects.create(organization=org, name="Scanner Ltd", code="SCAN")
    branch = Branch.objects.create(organization=org, company=company, name="Main", code="SCANMAIN")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="SCANPOS", location_type="pos")
    membership = Membership.objects.create(organization=org, user=user, status=MembershipStatus.ACTIVE)
    membership.branches.add(branch)
    grant_sale_permission(membership)
    category = Category.objects.create(organization=org, name="Phones", code="scan-phones")
    product = Product.objects.create(
        organization=org, category=category, name="A07 Phone", sku="A07-SCAN",
        barcode="600001", is_serialized=True, selling_price=18600, cost_price=15000,
    )
    stock_unit = StockUnit.objects.create(
        organization=org, product=product, location=location,
        serial_number="350151146420515", status=SerialStatus.AVAILABLE,
    )
    post_stock_movement(
        organization=org, product=product, location=location, quantity=1,
        movement_type=StockMovementType.OPENING, actor=user, stock_unit=stock_unit,
    )
    client.force_login(user)

    response = client.post(reverse("pos-cart-add"), {"lookup": stock_unit.serial_number, "quantity": "1"})

    line = Sale.objects.get(organization=org, status="draft").lines.get()
    assert response.status_code == 302
    assert line.product == product
    assert line.stock_unit == stock_unit
    assert line.quantity == 1


@pytest.mark.django_db
def test_cart_completion_can_create_inline_customer_for_paid_sale(client):
    user = User.objects.create_user(username="inlinecustomer", email="inline@example.com")
    org = Organization.objects.create(name="Inline Retail", slug="inline-retail", status="active")
    company = Company.objects.create(organization=org, name="Inline Ltd", code="INLINE")
    branch = Branch.objects.create(organization=org, company=company, name="Main", code="INMAIN")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="INPOS", location_type="pos")
    membership = Membership.objects.create(organization=org, user=user, status=MembershipStatus.ACTIVE)
    membership.branches.add(branch)
    grant_sale_permission(membership)
    category = Category.objects.create(organization=org, name="Accessories", code="inline-accessories")
    product = Product.objects.create(organization=org, category=category, name="Earphones", sku="EAR", selling_price=1200)
    post_stock_movement(
        organization=org, product=product, location=location, quantity=3,
        movement_type=StockMovementType.OPENING, actor=user,
    )
    client.force_login(user)
    client.post(reverse("pos-cart-add"), {"product": product.id, "quantity": "1"})
    cart = Sale.objects.get(organization=org, status="draft")

    response = client.post(reverse("pos-cart-complete", args=[cart.id]), {
        "cash_amount": "1200",
        "new_customer_name": "Brian Demo",
        "new_customer_phone": "0712000000",
    })

    cart.refresh_from_db()
    customer = Contact.objects.get(organization=org, name="Brian Demo")
    assert response.status_code == 302
    assert cart.status == "paid"
    assert cart.customer == customer
    assert customer.phone_number == "0712000000"


@pytest.mark.django_db
def test_cash_overtender_is_recorded_as_change_not_extra_payment(client):
    user = User.objects.create_user(username="changecashier", email="change@example.com")
    org = Organization.objects.create(name="Change Retail", slug="change-retail", status="active")
    company = Company.objects.create(organization=org, name="Change Ltd", code="CHANGE")
    branch = Branch.objects.create(organization=org, company=company, name="Main", code="CHMAIN")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="CHPOS", location_type="pos")
    membership = Membership.objects.create(organization=org, user=user, status=MembershipStatus.ACTIVE)
    membership.branches.add(branch)
    grant_sale_permission(membership)
    category = Category.objects.create(organization=org, name="Accessories", code="change-accessories")
    product = Product.objects.create(organization=org, category=category, name="Adapter", sku="ADAPT", selling_price=750)
    post_stock_movement(
        organization=org, product=product, location=location, quantity=3,
        movement_type=StockMovementType.OPENING, actor=user,
    )
    client.force_login(user)
    client.post(reverse("pos-cart-add"), {"product": product.id, "quantity": "2"})
    cart = Sale.objects.get(organization=org, status="draft")

    response = client.post(reverse("pos-cart-complete", args=[cart.id]), {"cash_amount": "2000"})

    cart.refresh_from_db()
    payment = Payment.objects.get(sale=cart)
    assert response.status_code == 302
    assert cart.total == 1500
    assert cart.paid_total == 1500
    assert payment.amount == 1500


@pytest.mark.django_db
def test_cart_completion_stores_credit_agency_and_next_of_kin_context(client):
    user = User.objects.create_user(username="agencycashier", email="agency@example.com")
    org = Organization.objects.create(name="Agency Retail", slug="agency-retail", status="active")
    company = Company.objects.create(organization=org, name="Agency Ltd", code="AGENCY")
    branch = Branch.objects.create(organization=org, company=company, name="Main", code="AGMAIN")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="AGPOS", location_type="pos")
    membership = Membership.objects.create(organization=org, user=user, status=MembershipStatus.ACTIVE, is_owner=True)
    membership.branches.add(branch)
    grant_sale_permission(membership)
    category = Category.objects.create(organization=org, name="Phones", code="agency-phones")
    product = Product.objects.create(organization=org, category=category, name="A07", sku="AG-A07", selling_price=19200)
    customer = Contact.objects.create(
        organization=org,
        contact_type="customer",
        name="Agency Customer",
        credit_limit=50000,
        payment_terms_days=30,
    )
    post_stock_movement(
        organization=org, product=product, location=location, quantity=3,
        movement_type=StockMovementType.OPENING, actor=user,
    )
    client.force_login(user)
    client.post(reverse("pos-cart-add"), {"product": product.id, "quantity": "1"})
    cart = Sale.objects.get(organization=org, status="draft")

    response = client.post(reverse("pos-cart-complete", args=[cart.id]), {
        "customer": customer.id,
        "cash_amount": "19200",
        "sale_channel": "credit",
        "credit_agency": "watu",
        "customer_national_id": "12345678",
        "next_of_kin_name": "Kin Customer",
        "next_of_kin_phone": "0712000000",
    })

    cart.refresh_from_db()
    assert response.status_code == 302
    assert cart.sale_channel == "credit"
    assert cart.credit_agency == "watu"
    assert cart.customer_national_id == "12345678"
    assert cart.next_of_kin_name == "Kin Customer"
    assert cart.next_of_kin_phone == "0712000000"


@pytest.mark.django_db
def test_non_cash_overtender_is_rejected(client):
    user = User.objects.create_user(username="cardreject", email="cardreject@example.com")
    org = Organization.objects.create(name="Card Retail", slug="card-retail", status="active")
    company = Company.objects.create(organization=org, name="Card Ltd", code="CARD")
    branch = Branch.objects.create(organization=org, company=company, name="Main", code="CARDMAIN")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="CARDPOS", location_type="pos")
    membership = Membership.objects.create(organization=org, user=user, status=MembershipStatus.ACTIVE, is_owner=True)
    membership.branches.add(branch)
    grant_sale_permission(membership)
    category = Category.objects.create(organization=org, name="Accessories", code="card-accessories")
    product = Product.objects.create(organization=org, category=category, name="Card Cable", sku="CARD-CABLE", selling_price=500)
    post_stock_movement(
        organization=org, product=product, location=location, quantity=3,
        movement_type=StockMovementType.OPENING, actor=user,
    )
    client.force_login(user)

    response = client.post(reverse("pos-checkout"), {
        "product": product.id,
        "quantity": "1",
        "card_amount": "600",
        "card_reference": "CARD-001",
    })

    assert response.status_code == 200
    assert b"Only cash payments can exceed the sale total for change." in response.content
    assert not Sale.objects.filter(organization=org).exists()


@pytest.mark.django_db
def test_cart_accepts_true_split_payment_with_references(client):
    user = User.objects.create_user(username="splitcashier", email="split@example.com")
    org = Organization.objects.create(name="Split Retail", slug="split-retail", status="active")
    company = Company.objects.create(organization=org, name="Split Ltd", code="SPLIT")
    branch = Branch.objects.create(organization=org, company=company, name="Main", code="SPLITMAIN")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="SPLITPOS", location_type="pos")
    membership = Membership.objects.create(organization=org, user=user, status=MembershipStatus.ACTIVE)
    membership.branches.add(branch)
    grant_sale_permission(membership)
    category = Category.objects.create(organization=org, name="Accessories", code="split-accessories")
    product = Product.objects.create(
        organization=org, category=category, name="Phone Case", sku="SPLIT-CASE", selling_price=2000,
    )
    post_stock_movement(
        organization=org, product=product, location=location, quantity=5,
        movement_type=StockMovementType.OPENING, actor=user,
    )
    client.force_login(user)

    client.post(reverse("pos-cart-add"), {"product": product.id, "quantity": "1"})
    cart = Sale.objects.get(organization=org, status="draft")
    response = client.post(reverse("pos-cart-complete", args=[cart.id]), {
        "cash_amount": "500",
        "mpesa_amount": "1000",
        "mpesa_reference": "MPESA-001",
        "card_amount": "500",
        "card_reference": "CARD-001",
    })

    cart.refresh_from_db()
    assert response.status_code == 302
    assert cart.status == "part_paid"
    assert cart.paid_total == 500
    assert set(Payment.objects.filter(sale=cart).values_list("method", "amount")) == {
        ("cash", 500), ("mpesa", 1000), ("card", 500),
    }
    assert Payment.objects.get(sale=cart, method="cash").status == PaymentStatus.CONFIRMED
    assert Payment.objects.get(sale=cart, method="mpesa").status == PaymentStatus.PENDING
    assert Payment.objects.get(sale=cart, method="card").status == PaymentStatus.PENDING


@pytest.mark.django_db
def test_cart_rejects_non_cash_allocation_without_reference(client):
    user = User.objects.create_user(username="referencecashier", email="reference@example.com")
    org = Organization.objects.create(name="Reference Retail", slug="reference-retail", status="active")
    company = Company.objects.create(organization=org, name="Reference Ltd", code="REF")
    branch = Branch.objects.create(organization=org, company=company, name="Main", code="REFMAIN")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="REFPOS", location_type="pos")
    membership = Membership.objects.create(organization=org, user=user, status=MembershipStatus.ACTIVE)
    membership.branches.add(branch)
    grant_sale_permission(membership)
    category = Category.objects.create(organization=org, name="Accessories", code="reference-accessories")
    product = Product.objects.create(
        organization=org, category=category, name="Cable", sku="REF-CABLE", selling_price=1000,
    )
    post_stock_movement(
        organization=org, product=product, location=location, quantity=5,
        movement_type=StockMovementType.OPENING, actor=user,
    )
    client.force_login(user)

    client.post(reverse("pos-cart-add"), {"product": product.id, "quantity": "1"})
    cart = Sale.objects.get(organization=org, status="draft")
    response = client.post(reverse("pos-cart-complete", args=[cart.id]), {
        "mpesa_amount": "1000",
    })

    cart.refresh_from_db()
    assert response.status_code == 302
    assert cart.status == "draft"
    assert not Payment.objects.filter(sale=cart).exists()


@pytest.mark.django_db
def test_discounted_cart_requires_owner_approval(client):
    user = User.objects.create_user(username="discountowner", email="discount@example.com")
    org = Organization.objects.create(name="Discount Retail", slug="discount-retail", status="active")
    company = Company.objects.create(organization=org, name="Discount Ltd", code="DISC")
    branch = Branch.objects.create(organization=org, company=company, name="Main", code="DISCMAIN")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="DISCPOS", location_type="pos")
    membership = Membership.objects.create(organization=org, user=user, status=MembershipStatus.ACTIVE, is_owner=True)
    membership.branches.add(branch)
    category = Category.objects.create(organization=org, name="Accessories", code="discount-accessories")
    product = Product.objects.create(organization=org, category=category, name="Cable", sku="DISC-CABLE", selling_price=1000)
    post_stock_movement(organization=org, product=product, location=location, quantity=2, movement_type=StockMovementType.OPENING, actor=user)
    client.force_login(user)

    client.post(reverse("pos-cart-add"), {"product": product.id, "quantity": "1", "discount": "100"})
    cart = Sale.objects.get(organization=org, status="draft")
    client.post(reverse("pos-cart-complete", args=[cart.id]), {"payment_method": "cash", "amount_received": "900"})
    cart.refresh_from_db()
    approval = ApprovalRequest.objects.get(target_id=str(cart.id), request_type="discount")
    assert cart.status == "draft"

    client.post(reverse("approval-decide", args=[approval.id]), {"decision": "approved"})
    response = client.post(reverse("pos-cart-complete", args=[cart.id]), {"payment_method": "cash", "amount_received": "900"})
    cart.refresh_from_db()
    assert response.status_code == 302
    assert cart.status == "paid"
    assert cart.discount_total == 100
    assert cart.total == 900

# Create your tests here.
