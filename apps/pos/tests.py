import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Category, Product
from apps.inventory.models import StockMovementType
from apps.inventory.services import post_stock_movement
from apps.organizations.models import Branch, Company, Location, Membership, MembershipStatus, Organization, Role
from apps.sales.models import Sale
from apps.contacts.models import Contact
from apps.operations.models import Receivable
from apps.operations.models import ApprovalRequest
from apps.pos.models import POSSession


def grant_sale_permission(membership):
    permission = Permission.objects.get(content_type__app_label="sales", codename="add_sale")
    role = Role.objects.create(organization=membership.organization, name="Cashier", code=f"cashier-{membership.user.username}")
    role.permissions.add(permission)
    membership.roles.add(role)


@pytest.mark.django_db
def test_unified_checkout_completes_quantity_sale(client):
    user = User.objects.create_user(username="cashier", email="checkout@example.com")
    org = Organization.objects.create(name="Retail", slug="checkout-retail", status="active")
    company = Company.objects.create(organization=org, name="Retail Ltd", code="RET")
    branch = Branch.objects.create(organization=org, company=company, name="Main", code="MAIN")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="POS", location_type="pos")
    membership = Membership.objects.create(organization=org, user=user, status=MembershipStatus.ACTIVE)
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
def test_checkout_creates_controlled_credit_receivable(client):
    user = User.objects.create_user(username="creditcashier", email="credit@example.com")
    org = Organization.objects.create(name="Credit Retail", slug="credit-retail", status="active")
    company = Company.objects.create(organization=org, name="Credit Ltd", code="CRED")
    branch = Branch.objects.create(organization=org, company=company, name="Main", code="CMAIN")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="CPOS", location_type="pos")
    membership = Membership.objects.create(organization=org, user=user, status=MembershipStatus.ACTIVE)
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
def test_owner_can_review_closed_cashier_session(client):
    from django.core.management import call_command
    call_command("seed_demo_data")
    owner = User.objects.get(username="alice")
    org = Organization.objects.get(slug="kipekee-electronics")
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
