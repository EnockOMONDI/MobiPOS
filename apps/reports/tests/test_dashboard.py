from decimal import Decimal

import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.commissions.models import CommissionAccrual, CommissionRule
from apps.inventory.models import SerialStatus, StockMovementType, StockUnit
from apps.inventory.services import post_stock_movement
from apps.organizations.models import AgentProfile, AgentProfileStatus, AgentProfileType, Branch, Company, Location, LocationType, Membership, MembershipStatus, Organization
from apps.catalog.models import Category, Product
from apps.pos.models import POSSession
from apps.sales.models import Sale, SaleLine, SaleStatus


@pytest.mark.django_db
def test_dashboard_serves_public_landing_for_anonymous_users(client):
    response = client.get(reverse("dashboard"))

    assert response.status_code == 200
    assert b"Run your mobile business" in response.content
    assert b"Try Demo Version Now" in response.content


@pytest.mark.django_db
def test_demo_access_page_lists_brian_demo_credentials(client):
    response = client.get(reverse("demo-access"))

    assert response.status_code == 200
    assert b"Login as Brian" in response.content
    assert b"brian" in response.content
    assert b"DemoPass123!" in response.content
    assert b"platformadmin" not in response.content
    assert b"AdminPass123!" not in response.content


@pytest.mark.django_db
def test_login_demo_page_does_not_expose_platform_admin_credentials(client):
    response = client.get(f"{reverse('login')}?demo=platformadmin")

    assert response.status_code == 200
    assert b"DemoPass123!" in response.content
    assert b"AdminPass123!" not in response.content


@pytest.mark.django_db
def test_product_features_page_explains_completion_statuses_and_scenarios(client):
    response = client.get(reverse("product-features"))

    assert response.status_code == 200
    assert b"What MobiPOS" in response.content
    assert b"does today." in response.content
    assert b"A clear guide to the workflows" not in response.content
    assert b"How to read this page" not in response.content
    assert b"Complete means the workflow" not in response.content
    assert b"Business scenario" in response.content
    assert b"IMEI and serial lifecycle tracking" in response.content
    assert b"Live M-Pesa confirmation and reconciliation" in response.content


@pytest.mark.django_db
def test_business_flow_page_handles_empty_database_and_keeps_nodes_clickable(client):
    response = client.get(reverse("business-flow"))

    assert response.status_code == 200
    assert b"How MobiPOS" in response.content
    assert b"runs the business." in response.content
    assert b"MobiPOS business flow" in response.content
    assert b"Quick navigation" not in response.content
    assert b"The demo account lets you open" not in response.content
    assert b"Each card explains one part" not in response.content
    assert b"View journey" in response.content
    assert b"Role-based flows" in response.content
    assert b"Business flow" in response.content
    assert b"Current status" not in response.content
    assert b"No seeded organization yet" not in response.content
    assert b"boardrooms" not in response.content
    assert b"Presentation ready" not in response.content
    assert reverse("purchase-create") in response.content.decode()
    assert reverse("batch-serial-intake") in response.content.decode()
    assert reverse("agent-network-report") in response.content.decode()
    assert "demo=brian&amp;next=/purchases/new/" in response.content.decode()


@pytest.mark.django_db
def test_business_flow_page_uses_rich_seeded_demo_data(client):
    from django.core.management import call_command

    call_command("seed_demo_data")

    response = client.get(reverse("business-flow"))

    assert response.status_code == 200
    assert response.context["flow_organization"].slug == "nairobi-mobile-hub"
    assert response.context["flow_counts"]["branches"] >= 3
    assert response.context["flow_counts"]["serialized_units"] >= 60
    assert response.context["flow_counts"]["audit_events"] >= 8
    assert b"Brian" in response.content
    assert b"Owner" in response.content
    assert b"Inventory Officer" in response.content
    assert b"Cashier" in response.content
    assert b"Field Agent" in response.content
    assert b"Direct Sales Agent" in response.content
    assert b"Technician" in response.content
    assert b"Create supplier purchase" in response.content
    assert b"Register IMEIs in batch" in response.content
    assert b"Allocate to agent" in response.content
    assert b"Review activity" in response.content
    assert b"Full Business Flow" in response.content
    assert b"Purchasing And Stock Intake Journey" in response.content
    assert b"Inventory, Transfer And Custody Journey" in response.content
    assert b"POS, Payments And Credit Journey" in response.content
    assert b"Agent And DSA Journey" in response.content
    assert b"After-Sales, Audit And Reporting Journey" in response.content
    assert b"Live M-Pesa confirmation and automatic reconciliation still need provider setup" not in response.content


@pytest.mark.django_db
def test_business_flow_page_renders_for_authenticated_demo_owner(client):
    from django.core.management import call_command

    call_command("seed_demo_data")
    brian = User.objects.get(username="brian")
    client.force_login(brian)

    response = client.get(reverse("business-flow"))

    assert response.status_code == 200
    assert b"MobiPOS business flow" in response.content
    assert b"Quick navigation" not in response.content
    assert b"The demo account lets you open" not in response.content
    assert b"Each card explains one part" not in response.content
    assert b"View journey" in response.content
    assert b"Full Business Flow" in response.content
    assert b"Role-based flows" in response.content


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
def test_agent_stock_module_only_shows_agent_custody_units(client):
    user = User.objects.create_user(username="agent-stock-owner", email="agent-stock@example.com")
    organization = Organization.objects.create(name="Agent Stock Org", slug="agent-stock-org", status="active")
    company = Company.objects.create(organization=organization, name="Agent Stock Ltd", code="ASL")
    branch = Branch.objects.create(organization=organization, company=company, name="Main", code="MAIN")
    membership = Membership.objects.create(
        user=user,
        organization=organization,
        status=MembershipStatus.ACTIVE,
        is_owner=True,
    )
    membership.branches.add(branch)
    agent_location = Location.objects.create(
        organization=organization,
        branch=branch,
        name="Agent Custody",
        code="AGENT",
        location_type=LocationType.AGENT,
        custodian_membership=membership,
    )
    warehouse_location = Location.objects.create(
        organization=organization,
        branch=branch,
        name="Warehouse",
        code="WH",
        location_type=LocationType.WAREHOUSE,
    )
    category = Category.objects.create(organization=organization, name="Phones", code="phones")
    product = Product.objects.create(organization=organization, category=category, name="A07", sku="A07", is_serialized=True)
    StockUnit.objects.create(
        organization=organization,
        product=product,
        location=agent_location,
        serial_number="AGENT-IMEI-001",
        status=SerialStatus.AVAILABLE,
    )
    StockUnit.objects.create(
        organization=organization,
        product=product,
        location=warehouse_location,
        serial_number="WAREHOUSE-IMEI-001",
        status=SerialStatus.AVAILABLE,
    )
    client.force_login(user)

    response = client.get(reverse("module-overview", args=["agent-stock"]))

    assert response.status_code == 200
    assert b"AGENT-IMEI-001" in response.content
    assert b"WAREHOUSE-IMEI-001" not in response.content


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

    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    client.force_login(owner)

    response = client.get(reverse("module-overview", args=["sales"]))

    assert response.context["rows"]
    assert all(row["detail_url"].startswith("/sales/") for row in response.context["rows"])


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


@pytest.mark.django_db
def test_agent_network_report_aggregates_stock_sales_profit_and_commissions(client):
    owner = User.objects.create_user(username="agent-report-owner", email="agent-report-owner@example.com")
    agent = User.objects.create_user(username="agent-report-user", email="agent-report-user@example.com", first_name="Report", last_name="Agent")
    organization = Organization.objects.create(name="Agent Report Org", slug="agent-report-org", status="active")
    company = Company.objects.create(organization=organization, name="Agent Report Ltd", code="ARL")
    branch = Branch.objects.create(organization=organization, company=company, name="Main", code="MAIN")
    owner_membership = Membership.objects.create(
        user=owner,
        organization=organization,
        status=MembershipStatus.ACTIVE,
        is_owner=True,
    )
    owner_membership.branches.add(branch)
    agent_membership = Membership.objects.create(
        user=agent,
        organization=organization,
        status=MembershipStatus.ACTIVE,
    )
    agent_membership.branches.add(branch)
    pos_location = Location.objects.create(organization=organization, branch=branch, name="Main POS", code="POS", location_type=LocationType.POS)
    agent_location = Location.objects.create(
        organization=organization,
        branch=branch,
        name="Report Agent Custody",
        code="AG-REPORT",
        location_type=LocationType.AGENT,
        custodian_membership=agent_membership,
    )
    category = Category.objects.create(organization=organization, name="Phones", code="phones")
    product = Product.objects.create(
        organization=organization,
        category=category,
        name="Report Phone",
        sku="REPORT-PHONE",
        is_serialized=True,
        selling_price=Decimal("20000.00"),
        cost_price=Decimal("15000.00"),
    )
    StockUnit.objects.create(
        organization=organization,
        product=product,
        location=agent_location,
        serial_number="AGENT-REPORT-STOCK",
        status=SerialStatus.AVAILABLE,
    )
    sold_unit = StockUnit.objects.create(
        organization=organization,
        product=product,
        location=pos_location,
        serial_number="AGENT-REPORT-SOLD",
        status=SerialStatus.SOLD,
    )
    profile = AgentProfile.objects.create(
        organization=organization,
        membership=agent_membership,
        profile_type=AgentProfileType.AGENT,
        status=AgentProfileStatus.ACTIVE,
        branch=branch,
        legal_name="Report Agent",
    )
    session = POSSession.objects.create(organization=organization, number="SES-AGENT-REPORT", location=pos_location, cashier=owner)
    sale = Sale.objects.create(
        organization=organization,
        number="SALE-AGENT-REPORT",
        session=session,
        location=pos_location,
        agent=agent,
        status=SaleStatus.PAID,
        subtotal=Decimal("20000.00"),
        total=Decimal("20000.00"),
        paid_total=Decimal("20000.00"),
        created_by=owner,
    )
    SaleLine.objects.create(
        organization=organization,
        sale=sale,
        product=product,
        stock_unit=sold_unit,
        quantity=1,
        unit_price=Decimal("20000.00"),
        unit_cost=Decimal("15000.00"),
        line_total=Decimal("20000.00"),
    )
    rule = CommissionRule.objects.create(
        organization=organization,
        name="Phone commission",
        product=product,
        fixed_amount=Decimal("1000.00"),
    )
    CommissionAccrual.objects.create(
        organization=organization,
        sale=sale,
        agent=agent,
        rule=rule,
        amount=Decimal("1000.00"),
        is_payable=True,
    )
    client.force_login(owner)

    response = client.get(reverse("agent-network-report"))
    csv_response = client.get(reverse("agent-network-report"), {"format": "csv"})

    assert response.status_code == 200
    assert response.context["summary"]["sales_total"] == Decimal("20000.00")
    assert response.context["summary"]["payable_commission"] == Decimal("1000.00")
    assert response.context["summary"]["stock_held"] == 1
    row = next(row for row in response.context["profile_rows"] if row["profile"] == profile)
    assert row["sale_count"] == 1
    assert row["gross_profit"] == Decimal("5000.00")
    assert row["stock_count"] == 1
    assert b"Report Agent" in response.content
    assert b"KES 5000" in response.content
    assert csv_response.status_code == 200
    assert b"Report Agent" in csv_response.content
    assert b"20000" in csv_response.content
