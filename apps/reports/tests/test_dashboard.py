import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.inventory.models import SerialStatus, StockMovementType, StockUnit
from apps.inventory.services import post_stock_movement
from apps.organizations.models import Branch, Company, Location, Membership, MembershipStatus, Organization
from apps.catalog.models import Category, Product
from apps.pos.models import POSSession
from apps.sales.models import Sale, SaleLine


@pytest.mark.django_db
def test_dashboard_requires_authentication(client):
    response = client.get(reverse("dashboard"))

    assert response.status_code == 302
    assert reverse("login") in response.url


@pytest.mark.django_db
def test_dashboard_resolves_active_organization(client):
    user = User.objects.create_user(
        username="owner", email="owner@example.com", password="password"
    )
    organization = Organization.objects.create(name="Acme", slug="acme", status="active")
    Membership.objects.create(
        user=user,
        organization=organization,
        status=MembershipStatus.ACTIVE,
        is_owner=True,
    )
    client.force_login(user)

    response = client.get(reverse("dashboard"))

    assert response.status_code == 200
    assert response.context["active_organization"] == organization


@pytest.mark.django_db
def test_module_overview_is_tenant_scoped(client):
    user = User.objects.create_user(username="owner", email="scope@example.com")
    first = Organization.objects.create(name="First", slug="first-scope", status="active")
    second = Organization.objects.create(name="Second", slug="second-scope", status="active")
    Membership.objects.create(user=user, organization=first, status=MembershipStatus.ACTIVE, is_owner=True)
    first_category = Category.objects.create(organization=first, name="Phones", code="phones")
    second_category = Category.objects.create(organization=second, name="Phones", code="phones")
    Product.objects.create(organization=first, category=first_category, name="Visible Product", sku="VISIBLE")
    Product.objects.create(organization=second, category=second_category, name="Hidden Product", sku="HIDDEN")
    client.force_login(user)

    response = client.get(reverse("module-overview", args=["products"]))

    assert b"Visible Product" in response.content
    assert b"Hidden Product" not in response.content


@pytest.mark.django_db
def test_module_overview_is_paginated(client):
    user = User.objects.create_user(username="page-owner", email="page@example.com")
    organization = Organization.objects.create(name="Paged", slug="paged", status="active")
    Membership.objects.create(
        user=user, organization=organization, status=MembershipStatus.ACTIVE, is_owner=True,
    )
    category = Category.objects.create(organization=organization, name="Phones", code="phones")
    for number in range(30):
        Product.objects.create(
            organization=organization, category=category,
            name=f"Product {number:02}", sku=f"SKU-{number:02}",
        )
    client.force_login(user)

    first_page = client.get(reverse("module-overview", args=["products"]))
    second_page = client.get(reverse("module-overview", args=["products"]), {"page": 2})

    assert len(first_page.context["rows"]) == 25
    assert len(second_page.context["rows"]) == 5
    assert first_page.context["page_obj"].paginator.count == 30


@pytest.mark.django_db
def test_product_register_supports_search_status_and_detail_links(client):
    user = User.objects.create_user(username="filter-owner", email="filter@example.com")
    organization = Organization.objects.create(name="Filter", slug="filter", status="active")
    Membership.objects.create(user=user, organization=organization, status=MembershipStatus.ACTIVE, is_owner=True)
    category = Category.objects.create(organization=organization, name="Phones", code="filter-phones")
    visible = Product.objects.create(organization=organization, category=category, name="Visible Phone", sku="VISIBLE")
    Product.objects.create(organization=organization, category=category, name="Archived Phone", sku="ARCHIVED", is_active=False)
    client.force_login(user)

    response = client.get(reverse("module-overview", args=["products"]), {"q": "Visible", "status": "active"})

    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 1
    assert response.context["rows"][0]["detail_url"] == reverse("product-detail", args=[visible.id])


@pytest.mark.django_db
def test_existing_operational_registers_expose_detail_links(client):
    from django.core.management import call_command
    from apps.sales.models import Sale

    call_command("seed_demo_data")
    owner = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="mobipos-electronics")
    sale = Sale.objects.filter(organization=organization).first()
    client.force_login(owner)

    response = client.get(reverse("module-overview", args=["sales"]))

    assert reverse("sale-detail", args=[sale.id]) in {
        row["detail_url"] for row in response.context["rows"]
    }


@pytest.mark.django_db
def test_dashboard_exposes_sysco_style_operational_cards_and_latest_sales(client):
    user = User.objects.create_user(username="sysco-dashboard", email="sysco-dashboard@example.com")
    organization = Organization.objects.create(name="Sysco Better", slug="sysco-better", status="active")
    company = Company.objects.create(organization=organization, name="Sysco Better Ltd", code="SBL")
    branch = Branch.objects.create(organization=organization, company=company, name="Main", code="MAIN")
    location = Location.objects.create(organization=organization, branch=branch, name="POS", code="POS", location_type="pos")
    Membership.objects.create(user=user, organization=organization, status=MembershipStatus.ACTIVE, is_owner=True).branches.add(branch)
    category = Category.objects.create(organization=organization, name="Phones", code="phones")
    product = Product.objects.create(
        organization=organization,
        category=category,
        name="A07 64GB/4GB",
        sku="A07",
        is_serialized=True,
        selling_price=19200,
    )
    unit = StockUnit.objects.create(
        organization=organization,
        product=product,
        location=location,
        serial_number="351481187096109",
        status=SerialStatus.AVAILABLE,
    )
    post_stock_movement(
        organization=organization,
        product=product,
        location=location,
        quantity=1,
        movement_type=StockMovementType.OPENING,
        actor=user,
        stock_unit=unit,
    )
    session = POSSession.objects.create(organization=organization, number="SES-DASH", location=location, cashier=user)
    sale = Sale.objects.create(
        organization=organization,
        number="SALE-DASH",
        session=session,
        location=location,
        agent=user,
        created_by=user,
        total=19200,
        paid_total=19200,
        status="paid",
    )
    SaleLine.objects.create(
        organization=organization,
        sale=sale,
        product=product,
        stock_unit=unit,
        quantity=1,
        unit_price=19200,
        line_total=19200,
    )
    client.force_login(user)

    response = client.get(reverse("dashboard"))

    assert response.status_code == 200
    assert "latest_sales" in response.context
    assert b"Latest sales" in response.content
    assert b"A07 64GB/4GB" in response.content
    assert b"351481187096109" in response.content
    assert b"IMEI history" in response.content


@pytest.mark.django_db
def test_imei_history_finds_serial_and_links_to_sale(client):
    user = User.objects.create_user(username="imei-owner", email="imei@example.com")
    organization = Organization.objects.create(name="IMEI Org", slug="imei-org", status="active")
    company = Company.objects.create(organization=organization, name="IMEI Ltd", code="IMEI")
    branch = Branch.objects.create(organization=organization, company=company, name="Main", code="IMEIMAIN")
    location = Location.objects.create(organization=organization, branch=branch, name="POS", code="IMEIPOS", location_type="pos")
    Membership.objects.create(user=user, organization=organization, status=MembershipStatus.ACTIVE, is_owner=True).branches.add(branch)
    category = Category.objects.create(organization=organization, name="Phones", code="imei-phones")
    product = Product.objects.create(organization=organization, category=category, name="A06", sku="A06", is_serialized=True)
    unit = StockUnit.objects.create(
        organization=organization,
        product=product,
        location=location,
        serial_number="350584199018200",
        status=SerialStatus.AVAILABLE,
    )
    post_stock_movement(
        organization=organization,
        product=product,
        location=location,
        quantity=1,
        movement_type=StockMovementType.OPENING,
        actor=user,
        stock_unit=unit,
    )
    client.force_login(user)

    response = client.get(reverse("imei-history"), {"q": "350584199018200"})

    assert response.status_code == 200
    assert b"350584199018200" in response.content
    assert b"Movement timeline" in response.content
