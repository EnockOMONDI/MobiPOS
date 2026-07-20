import pytest
from io import BytesIO
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse
from openpyxl import Workbook

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
def test_stock_unit_rejects_primary_secondary_imei_conflicts():
    org = Organization.objects.create(name="Serial Unique Org", slug="serial-unique-org", status="active")
    company = Company.objects.create(organization=org, name="Company", code="CO")
    branch = Branch.objects.create(organization=org, company=company, name="Branch", code="BR")
    location = Location.objects.create(organization=org, branch=branch, name="Source", code="SRC", location_type="warehouse")
    category = Category.objects.create(organization=org, name="Phones", code="phones")
    phone = Product.objects.create(organization=org, category=category, name="Phone", sku="PH", is_serialized=True)
    StockUnit.objects.create(
        organization=org, product=phone, serial_number="IMEI-1", secondary_serial="IMEI-2",
        location=location, status=SerialStatus.AVAILABLE,
    )

    with pytest.raises(ValidationError, match="already exists"):
        StockUnit.objects.create(
            organization=org, product=phone, serial_number="IMEI-2",
            location=location, status=SerialStatus.AVAILABLE,
        )
    with pytest.raises(ValidationError, match="already exists"):
        StockUnit.objects.create(
            organization=org, product=phone, serial_number="IMEI-3", secondary_serial="IMEI-1",
            location=location, status=SerialStatus.AVAILABLE,
        )
    with pytest.raises(ValidationError, match="must be different"):
        StockUnit.objects.create(
            organization=org, product=phone, serial_number="IMEI-4", secondary_serial="IMEI-4",
            location=location, status=SerialStatus.AVAILABLE,
        )


@pytest.mark.django_db
def test_owner_approved_adjustment_posts_ledger_movement(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    org = Organization.objects.get(slug="nairobi-mobile-hub")
    product = Product.objects.get(organization=org, sku="CHG-20W")
    location = Location.objects.filter(organization=org, location_type="warehouse").order_by("code").first()
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
def test_batch_serial_intake_accepts_pasted_scanner_lines(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    org = Organization.objects.get(slug="nairobi-mobile-hub")
    product = Product.objects.filter(organization=org, is_serialized=True).first()
    location = Location.objects.filter(organization=org, location_type="warehouse").order_by("code").first()
    client.force_login(user)

    response = client.post(reverse("batch-serial-intake"), {
        "product": product.id,
        "location": location.id,
        "unit_cost": "12000.00",
        "serial_numbers": "BATCH-IMEI-001\nBATCH-IMEI-002",
        "reason": "Opening warehouse intake",
    })

    assert response.status_code == 200
    assert StockUnit.objects.filter(organization=org, serial_number__in=["BATCH-IMEI-001", "BATCH-IMEI-002"]).count() == 2
    assert StockBalance.objects.get(organization=org, product=product, location=location).quantity >= 2
    assert StockMovement.objects.filter(organization=org, reference_type="batch_serial_intake").count() == 2


@pytest.mark.django_db
def test_batch_serial_intake_reports_duplicate_rows_without_blocking_valid_rows(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    org = Organization.objects.get(slug="nairobi-mobile-hub")
    product = Product.objects.filter(organization=org, is_serialized=True).first()
    location = Location.objects.filter(organization=org, location_type="warehouse").order_by("code").first()
    existing = StockUnit.objects.filter(organization=org).first()
    client.force_login(user)

    response = client.post(reverse("batch-serial-intake"), {
        "product": product.id,
        "location": location.id,
        "serial_numbers": f"VALID-BATCH-001\nVALID-BATCH-001\n{existing.serial_number}",
        "reason": "Mixed quality intake",
    })

    assert response.status_code == 200
    assert StockUnit.objects.filter(organization=org, serial_number="VALID-BATCH-001").exists()
    assert response.context["result"]["failures"]
    assert len(response.context["result"]["created_units"]) == 1


@pytest.mark.django_db
def test_batch_serial_intake_accepts_csv_upload(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    org = Organization.objects.get(slug="nairobi-mobile-hub")
    product = Product.objects.filter(organization=org, is_serialized=True).first()
    location = Location.objects.filter(organization=org, location_type="warehouse").order_by("code").first()
    upload = SimpleUploadedFile(
        "serials.csv",
        b"serial_number,secondary_serial\nCSV-IMEI-001,CSV-IMEI-001B\nCSV-IMEI-002,\n",
        content_type="text/csv",
    )
    client.force_login(user)

    response = client.post(reverse("batch-serial-intake"), {
        "product": product.id,
        "location": location.id,
        "csv_file": upload,
        "reason": "CSV intake",
    })

    assert response.status_code == 200
    assert StockUnit.objects.filter(organization=org, serial_number__in=["CSV-IMEI-001", "CSV-IMEI-002"]).count() == 2
    assert StockUnit.objects.get(organization=org, serial_number="CSV-IMEI-001").secondary_serial == "CSV-IMEI-001B"


@pytest.mark.django_db
def test_batch_serial_intake_accepts_excel_upload(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    org = Organization.objects.get(slug="nairobi-mobile-hub")
    product = Product.objects.filter(organization=org, is_serialized=True).first()
    location = Location.objects.filter(organization=org, location_type="warehouse").order_by("code").first()
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.append(["serial_number", "secondary_serial"])
    worksheet.append(["XLSX-IMEI-001", "XLSX-IMEI-001B"])
    worksheet.append(["XLSX-IMEI-002", ""])
    buffer = BytesIO()
    workbook.save(buffer)
    upload = SimpleUploadedFile(
        "serials.xlsx",
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    client.force_login(user)

    response = client.post(reverse("batch-serial-intake"), {
        "product": product.id,
        "location": location.id,
        "csv_file": upload,
        "reason": "Excel intake",
    })

    assert response.status_code == 200
    assert StockUnit.objects.filter(organization=org, serial_number__in=["XLSX-IMEI-001", "XLSX-IMEI-002"]).count() == 2
    assert StockUnit.objects.get(organization=org, serial_number="XLSX-IMEI-001").secondary_serial == "XLSX-IMEI-001B"


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


@pytest.mark.django_db
def test_domain_owned_stock_movement_cannot_be_reversed_generically():
    user = User.objects.create_user(username="domain-reverser", email="domain-reverser@example.com")
    org = Organization.objects.create(name="Domain Reverse Org", slug="domain-reverse-org", status="active")
    company = Company.objects.create(organization=org, name="Company", code="CO")
    branch = Branch.objects.create(organization=org, company=company, name="Branch", code="BR")
    location = Location.objects.create(organization=org, branch=branch, name="Warehouse", code="WH", location_type="warehouse")
    category = Category.objects.create(organization=org, name="Items", code="items")
    product = Product.objects.create(organization=org, category=category, name="Item", sku="ITEM")
    post_stock_movement(
        organization=org, product=product, location=location, quantity=5,
        movement_type=StockMovementType.OPENING, actor=user,
    )
    movement = post_stock_movement(
        organization=org, product=product, location=location, quantity=-1,
        movement_type=StockMovementType.SALE, actor=user, reference_type="sale", reference_id="sale-1",
    )

    with pytest.raises(ValidationError, match="source workflow"):
        reverse_stock_movement(movement=movement, actor=user, reason="Wrong sale")

# Create your tests here.
