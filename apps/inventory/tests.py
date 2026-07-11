import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Category, Product
from apps.inventory.models import SerialStatus, StockAdjustment, StockBalance, StockMovement, StockMovementType, StockUnit
from apps.inventory.services import post_stock_movement, reverse_stock_movement
from apps.organizations.models import Branch, Company, Location, Organization


@pytest.mark.django_db
def test_stock_ledger_prevents_negative_stock():
    user = User.objects.create_user(username="stock", email="stock@example.com")
    org = Organization.objects.create(name="Org", slug="org", status="active")
    company = Company.objects.create(organization=org, name="Company", code="CO")
    branch = Branch.objects.create(organization=org, company=company, name="Branch", code="BR")
    location = Location.objects.create(organization=org, branch=branch, name="Warehouse", code="WH", location_type="warehouse")
    category = Category.objects.create(organization=org, name="Accessories", code="accessories")
    product = Product.objects.create(organization=org, category=category, name="Charger", sku="CHG")

    post_stock_movement(organization=org, product=product, location=location, quantity=5, movement_type=StockMovementType.OPENING, actor=user)
    with pytest.raises(ValidationError):
        post_stock_movement(organization=org, product=product, location=location, quantity=-6, movement_type=StockMovementType.SALE, actor=user)

    assert StockBalance.objects.get(product=product, location=location).quantity == 5


@pytest.mark.django_db
def test_stock_ledger_rejects_serial_from_another_product_or_location():
    user = User.objects.create_user(username="serial-stock", email="serial@example.com")
    org = Organization.objects.create(name="Serial Org", slug="serial-org", status="active")
    company = Company.objects.create(organization=org, name="Company", code="CO")
    branch = Branch.objects.create(organization=org, company=company, name="Branch", code="BR")
    source = Location.objects.create(organization=org, branch=branch, name="Source", code="SRC", location_type="warehouse")
    other = Location.objects.create(organization=org, branch=branch, name="Other", code="OTH", location_type="warehouse")
    category = Category.objects.create(organization=org, name="Phones", code="phones")
    phone = Product.objects.create(organization=org, category=category, name="Phone", sku="PH", is_serialized=True)
    tablet = Product.objects.create(organization=org, category=category, name="Tablet", sku="TB", is_serialized=True)
    unit = StockUnit.objects.create(
        organization=org, product=phone, serial_number="IMEI-1", location=source, status=SerialStatus.AVAILABLE
    )

    with pytest.raises(ValidationError):
        post_stock_movement(
            organization=org, product=tablet, location=source, quantity=-1,
            movement_type=StockMovementType.SALE, actor=user, stock_unit=unit,
        )
    with pytest.raises(ValidationError):
        post_stock_movement(
            organization=org, product=phone, location=other, quantity=-1,
            movement_type=StockMovementType.SALE, actor=user, stock_unit=unit,
        )


@pytest.mark.django_db
def test_owner_approved_adjustment_posts_ledger_movement(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="alice")
    org = Organization.objects.get(slug="mobipos-electronics")
    product = Product.objects.get(organization=org, sku="CHG-20W")
    location = Location.objects.get(organization=org, location_type="warehouse")
    client.force_login(user)

    response = client.post(reverse("stock-adjustment-create"), {
        "product": product.id, "location": location.id, "quantity": "4", "reason": "Count correction",
    })
    adjustment = StockAdjustment.objects.get(organization=org)
    client.post(reverse("stock-adjustment-complete", args=[adjustment.id]))
    adjustment.refresh_from_db()

    assert response.status_code == 302
    assert adjustment.status == "completed"
    assert adjustment.movement.movement_type == StockMovementType.ADJUSTMENT
    assert StockBalance.objects.get(organization=org, product=product, location=location).quantity == 4


@pytest.mark.django_db
def test_stock_movement_reversal_is_linked_and_cannot_repeat():
    user = User.objects.create_user(username="reverser", email="reverser@example.com")
    org = Organization.objects.create(name="Reverse Org", slug="reverse-org", status="active")
    company = Company.objects.create(organization=org, name="Company", code="CO")
    branch = Branch.objects.create(organization=org, company=company, name="Branch", code="BR")
    location = Location.objects.create(organization=org, branch=branch, name="Warehouse", code="WH", location_type="warehouse")
    category = Category.objects.create(organization=org, name="Items", code="items")
    product = Product.objects.create(organization=org, category=category, name="Item", sku="ITEM")
    movement = post_stock_movement(
        organization=org, product=product, location=location, quantity=5,
        movement_type=StockMovementType.OPENING, actor=user,
    )

    reversal = reverse_stock_movement(movement=movement, actor=user, reason="Opening error")

    assert reversal.reversed_movement == movement
    assert StockBalance.objects.get(organization=org, product=product, location=location).quantity == 0
    with pytest.raises(ValidationError):
        reverse_stock_movement(movement=movement, actor=user, reason="Repeat")

# Create your tests here.
