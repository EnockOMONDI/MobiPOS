import pytest
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Category, Product
from apps.audit.models import AuditEvent
from apps.contacts.models import Contact
from apps.inventory.models import SerialStatus, StockMovementType, StockUnit
from apps.inventory.services import post_stock_movement
from apps.organizations.models import Branch, Company, Location, LocationType, Membership, MembershipStatus, Organization, Role
from apps.pos.models import POSSession
from apps.sales.models import Sale, SaleLine, SaleStatus


@pytest.mark.django_db
def test_owner_can_create_catalog_and_contact_records(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    client.force_login(owner)

    client.post(reverse("category-create"), {"name": "New Category", "code": "new-category"})
    category = Category.objects.get(organization=organization, code="new-category")
    response = client.post(reverse("product-create"), {
        "category": category.id, "name": "New Item", "sku": "NEW-ITEM",
        "is_stocked": "on", "is_sellable": "on", "is_purchasable": "on", "is_active": "on",
        "reorder_level": "2", "cost_price": "100", "selling_price": "150", "tax_rate": "16",
        "warranty_days": "0",
    })
    client.post(reverse("contact-create"), {
        "contact_type": "customer", "name": "New Customer", "credit_limit": "5000",
        "payment_terms_days": "30", "is_active": "on",
    })

    assert response.status_code == 302
    assert Product.objects.filter(organization=organization, sku="NEW-ITEM").exists()
    assert Contact.objects.filter(organization=organization, name="New Customer").exists()


@pytest.mark.django_db
def test_product_create_returns_to_safe_purchase_setup_flow(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    category = Category.objects.get(organization=organization, code="phones")
    client.force_login(owner)

    response = client.post(reverse("product-create"), {
        "category": category.id,
        "name": "Return Flow Phone",
        "sku": "RETURN-FLOW-PHONE",
        "is_serialized": "on",
        "is_stocked": "on",
        "is_sellable": "on",
        "is_purchasable": "on",
        "is_active": "on",
        "reorder_level": "1",
        "cost_price": "10000",
        "selling_price": "12500",
        "tax_rate": "16",
        "warranty_days": "365",
        "next": reverse("purchase-create"),
    })

    assert response.status_code == 302
    assert response.url == reverse("purchase-create")
    assert Product.objects.filter(organization=organization, sku="RETURN-FLOW-PHONE").exists()


@pytest.mark.django_db
def test_duplicate_catalog_identifiers_are_form_errors(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    client.force_login(owner)

    category_response = client.post(reverse("category-create"), {"name": "Duplicate", "code": "phones"})
    product_response = client.post(reverse("product-create"), {
        "category": Category.objects.get(organization=organization, code="phones").id,
        "name": "Duplicate Phone",
        "sku": "MP-A07-64",
        "is_stocked": "on",
        "is_sellable": "on",
        "is_purchasable": "on",
        "is_active": "on",
    })

    assert category_response.status_code == 200
    assert b"This category code is already in use." in category_response.content
    assert product_response.status_code == 200
    assert b"This product SKU is already in use." in product_response.content


@pytest.mark.django_db
def test_product_lifecycle_is_tenant_scoped_and_audited(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    product = Product.objects.filter(organization=organization).first()
    other_org = Organization.objects.create(name="Other Catalog Tenant", slug="other-catalog-tenant", status="active")
    other_category = Category.objects.create(organization=other_org, name="Other Phones", code="other-phones")
    other_product = Product.objects.create(
        organization=other_org,
        category=other_category,
        name="Other Tenant Phone",
        sku="OTHER-PHONE",
    )
    client.force_login(owner)

    detail = client.get(reverse("product-detail", args=[product.id]))
    forbidden = client.get(reverse("product-detail", args=[other_product.id]))
    update = client.post(reverse("product-update", args=[product.id]), {
        "category": product.category_id,
        "brand": product.brand_id or "",
        "name": f"{product.name} Updated",
        "sku": product.sku,
        "barcode": product.barcode,
        "description": product.description,
        "is_serialized": "on" if product.is_serialized else "",
        "is_stocked": "on" if product.is_stocked else "",
        "is_sellable": "on" if product.is_sellable else "",
        "is_purchasable": "on" if product.is_purchasable else "",
        "warranty_days": product.warranty_days,
        "reorder_level": product.reorder_level,
        "cost_price": product.cost_price,
        "selling_price": product.selling_price,
        "tax_rate": product.tax_rate,
        "is_active": "on",
    })
    client.post(reverse("product-toggle-active", args=[product.id]))

    product.refresh_from_db()
    assert detail.status_code == 200
    assert forbidden.status_code == 404
    assert update.status_code == 302
    assert not product.is_active
    assert AuditEvent.objects.filter(target_id=str(product.id), action="product.updated").exists()
    assert AuditEvent.objects.filter(target_id=str(product.id), action="product.archived").exists()


def _create_product_visibility_fixture():
    owner = User.objects.create_user(username="product-owner", email="product-owner@example.com")
    viewer = User.objects.create_user(username="product-viewer", email="product-viewer@example.com")
    organization = Organization.objects.create(name="Product Scope", slug="product-scope", status="active")
    company = Company.objects.create(organization=organization, name="Product Scope Ltd", code="PSL")
    branch_a = Branch.objects.create(organization=organization, company=company, name="Nairobi", code="NBO")
    branch_b = Branch.objects.create(organization=organization, company=company, name="Mombasa", code="MBA")
    location_a = Location.objects.create(
        organization=organization,
        branch=branch_a,
        name="Nairobi Warehouse",
        code="NBO-WH",
        location_type=LocationType.WAREHOUSE,
    )
    location_b = Location.objects.create(
        organization=organization,
        branch=branch_b,
        name="Mombasa Warehouse",
        code="MBA-WH",
        location_type=LocationType.WAREHOUSE,
    )
    owner_membership = Membership.objects.create(
        user=owner,
        organization=organization,
        status=MembershipStatus.ACTIVE,
        is_owner=True,
    )
    owner_membership.branches.add(branch_a, branch_b)
    view_role = Role.objects.create(organization=organization, name="Product Viewer", code="product-viewer")
    view_role.permissions.add(Permission.objects.get(codename="view_product", content_type__app_label="catalog"))
    viewer_membership = Membership.objects.create(
        user=viewer,
        organization=organization,
        status=MembershipStatus.ACTIVE,
    )
    viewer_membership.roles.add(view_role)
    viewer_membership.branches.add(branch_a)
    category = Category.objects.create(organization=organization, name="Phones", code="phones")
    product = Product.objects.create(
        organization=organization,
        category=category,
        name="Galaxy Trace",
        sku="TRACE-001",
        barcode="BAR-TRACE-001",
        is_serialized=True,
        reorder_level=2,
        cost_price="501.00",
        selling_price="799.00",
    )
    unit_a = StockUnit.objects.create(
        organization=organization,
        product=product,
        location=location_a,
        serial_number="NBO-IMEI-001",
        status=SerialStatus.AVAILABLE,
        unit_cost="501.00",
    )
    unit_b = StockUnit.objects.create(
        organization=organization,
        product=product,
        location=location_b,
        serial_number="MBA-IMEI-001",
        status=SerialStatus.AVAILABLE,
        unit_cost="501.00",
    )
    post_stock_movement(
        organization=organization,
        product=product,
        location=location_a,
        quantity=1,
        movement_type=StockMovementType.OPENING,
        actor=owner,
        stock_unit=unit_a,
        unit_cost="501.00",
    )
    post_stock_movement(
        organization=organization,
        product=product,
        location=location_b,
        quantity=1,
        movement_type=StockMovementType.OPENING,
        actor=owner,
        stock_unit=unit_b,
        unit_cost="501.00",
    )
    sold_unit = StockUnit.objects.create(
        organization=organization,
        product=product,
        location=location_a,
        serial_number="NBO-IMEI-SOLD",
        status=SerialStatus.SOLD,
        unit_cost="501.00",
    )
    session = POSSession.objects.create(organization=organization, number="SES-PROD", location=location_a, cashier=owner)
    sale = Sale.objects.create(
        organization=organization,
        number="SALE-PROD",
        session=session,
        location=location_a,
        agent=owner,
        status=SaleStatus.PAID,
        total="799.00",
        paid_total="799.00",
        created_by=owner,
    )
    SaleLine.objects.create(
        organization=organization,
        sale=sale,
        product=product,
        stock_unit=sold_unit,
        quantity=1,
        unit_price="799.00",
        unit_cost="501.00",
        line_total="799.00",
    )
    return owner, viewer, product, branch_a, branch_b


@pytest.mark.django_db
def test_product_register_uses_scoped_stock_and_imei_workflow_data(client):
    owner, viewer, product, _branch_a, _branch_b = _create_product_visibility_fixture()

    client.force_login(owner)
    owner_response = client.get(reverse("module-overview", args=["products"]), {"q": "TRACE"})

    assert owner_response.status_code == 200
    assert owner_response.context["rows"][0]["stock_total"] == 2
    assert owner_response.context["rows"][0]["available_serial_count"] == 2
    assert owner_response.context["rows"][0]["sold_serial_count"] == 1
    assert b"Margin" in owner_response.content
    assert reverse("product-detail", args=[product.id]) == owner_response.context["rows"][0]["detail_url"]

    client.force_login(viewer)
    viewer_response = client.get(reverse("module-overview", args=["products"]), {"q": "TRACE"})

    assert viewer_response.status_code == 200
    assert viewer_response.context["rows"][0]["stock_total"] == 1
    assert viewer_response.context["rows"][0]["available_serial_count"] == 1
    assert viewer_response.context["rows"][0]["sold_serial_count"] == 1
    assert b"Margin" not in viewer_response.content
    assert b"Cost visibility" in viewer_response.content
    assert b"Restricted" in viewer_response.content


@pytest.mark.django_db
def test_product_detail_hides_costs_and_limits_locations_for_product_only_users(client):
    owner, viewer, product, _branch_a, _branch_b = _create_product_visibility_fixture()

    client.force_login(viewer)
    viewer_response = client.get(reverse("product-detail", args=[product.id]))

    assert viewer_response.status_code == 200
    assert b"Nairobi Warehouse" in viewer_response.content
    assert b"NBO-IMEI-001" in viewer_response.content
    assert b"Mombasa Warehouse" not in viewer_response.content
    assert b"MBA-IMEI-001" not in viewer_response.content
    assert b"Cost restricted" in viewer_response.content
    assert b"Cost price" not in viewer_response.content
    assert b"501.00" not in viewer_response.content

    client.force_login(owner)
    owner_response = client.get(reverse("product-detail", args=[product.id]))

    assert owner_response.status_code == 200
    assert b"Nairobi Warehouse" in owner_response.content
    assert b"Mombasa Warehouse" in owner_response.content
    assert b"Cost price" in owner_response.content
    assert b"501.00" in owner_response.content


@pytest.mark.django_db
def test_product_detail_links_to_complete_imei_register_when_preview_is_limited(client):
    owner, _viewer, product, branch_a, _branch_b = _create_product_visibility_fixture()
    location = Location.objects.get(organization=product.organization, branch=branch_a, code="NBO-WH")
    StockUnit.objects.bulk_create([
        StockUnit(
            organization=product.organization,
            product=product,
            location=location,
            serial_number=f"NBO-IMEI-{index:03d}",
            status=SerialStatus.AVAILABLE,
        )
        for index in range(2, 11)
    ])
    client.force_login(owner)

    response = client.get(reverse("product-detail", args=[product.id]))

    assert response.status_code == 200
    assert response.context["available_units_count"] == 11
    assert len(response.context["available_units"]) == 10
    assert f'{reverse("imei-history")}?q=TRACE-001&status=available'.encode() in response.content


@pytest.mark.django_db
def test_seeded_product_images_render_on_register_and_detail(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    product = Product.objects.get(organization=organization, sku="MP-A07-64")
    client.force_login(owner)

    register_response = client.get(reverse("module-overview", args=["products"]), {"q": "A07"})
    detail_response = client.get(reverse("product-detail", args=[product.id]))

    assert product.image_url == "/static/img/products/a07-phone.svg"
    assert register_response.status_code == 200
    assert detail_response.status_code == 200
    assert b"/static/img/products/a07-phone.svg" in register_response.content
    assert b"/static/img/products/a07-phone.svg" in detail_response.content
