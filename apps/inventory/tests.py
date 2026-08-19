import pytest
from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from openpyxl import Workbook

from apps.accounts.models import User
from apps.catalog.models import Category, Product
from apps.inventory.aging import execute_aged_stock_action, request_aged_stock_action, sync_aged_stock_action_from_approval
from apps.inventory.models import AgedStockAction, AgedStockActionStatus, SerialStatus, StockAdjustment, StockBalance, StockMovement, StockMovementType, StockUnit, StockUnitOffer
from apps.operations.models import ApprovalStatus
from apps.inventory.services import post_stock_movement, reverse_stock_movement
from apps.organizations.models import Branch, Company, Location, Membership, MembershipStatus, Organization


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


@pytest.mark.django_db
def test_aged_stock_action_creates_approval_and_syncs_decision():
    org = Organization.objects.create(name="Aged Action Org", slug="aged-action-org", status="active")
    manager = User.objects.create_user(username="aged-manager", email="manager@example.com")
    owner = User.objects.create_user(username="aged-owner", email="owner@example.com")
    Membership.objects.create(organization=org, user=manager, status=MembershipStatus.ACTIVE)
    Membership.objects.create(organization=org, user=owner, status=MembershipStatus.ACTIVE, is_owner=True)
    company = Company.objects.create(organization=org, name="Company", code="CO")
    branch = Branch.objects.create(organization=org, company=company, name="Branch", code="BR")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="POS", location_type="pos")
    category = Category.objects.create(organization=org, name="Phones", code="phones")
    product = Product.objects.create(organization=org, category=category, name="Phone", sku="PHONE", is_serialized=True)
    unit = StockUnit.objects.create(
        organization=org,
        product=product,
        serial_number="AGED-ACTION-001",
        location=location,
        status=SerialStatus.AVAILABLE,
    )

    action = request_aged_stock_action(
        stock_unit=unit,
        action_type="transfer",
        reason="Move this model to a faster branch.",
        next_step="Transfer to CBD.",
        requested_by=manager,
    )

    assert action.status == AgedStockActionStatus.REQUESTED
    assert action.approval is not None
    assert action.approval.request_type == "aged_stock_action"
    assert action.approval.branch == branch

    approval = action.approval
    approval.status = ApprovalStatus.APPROVED
    approval.decided_by = owner
    approval.decided_at = timezone.now()
    approval.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])

    synced = sync_aged_stock_action_from_approval(approval=approval)

    assert synced.status == AgedStockActionStatus.APPROVED
    assert synced.approved_by == owner


@pytest.mark.django_db
def test_aged_stock_action_reuses_active_request():
    org = Organization.objects.create(name="Aged Existing Org", slug="aged-existing-org", status="active")
    manager = User.objects.create_user(username="aged-existing-manager", email="manager@example.com")
    Membership.objects.create(organization=org, user=manager, status=MembershipStatus.ACTIVE)
    company = Company.objects.create(organization=org, name="Company", code="CO")
    branch = Branch.objects.create(organization=org, company=company, name="Branch", code="BR")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="POS", location_type="pos")
    category = Category.objects.create(organization=org, name="Phones", code="phones")
    product = Product.objects.create(organization=org, category=category, name="Phone", sku="PHONE", is_serialized=True)
    unit = StockUnit.objects.create(
        organization=org,
        product=product,
        serial_number="AGED-EXISTING-001",
        location=location,
        status=SerialStatus.AVAILABLE,
    )

    first = request_aged_stock_action(
        stock_unit=unit,
        action_type="discount",
        reason="Run a promotion.",
        requested_by=manager,
    )
    second = request_aged_stock_action(
        stock_unit=unit,
        action_type="transfer",
        reason="Transfer instead.",
        requested_by=manager,
    )

    assert first == second
    assert AgedStockAction.objects.filter(stock_unit=unit).count() == 1


def _approved_aged_action(*, action_type, proposal):
    org = Organization.objects.create(name=f"Execute {action_type}", slug=f"execute-{action_type}", status="active")
    manager = User.objects.create_user(username=f"manager-{action_type}", email=f"manager-{action_type}@example.com")
    owner = User.objects.create_user(username=f"owner-{action_type}", email=f"owner-{action_type}@example.com")
    Membership.objects.create(organization=org, user=manager, status=MembershipStatus.ACTIVE)
    Membership.objects.create(organization=org, user=owner, status=MembershipStatus.ACTIVE, is_owner=True)
    company = Company.objects.create(organization=org, name="Company", code="CO")
    branch = Branch.objects.create(organization=org, company=company, name="Source", code="SRC")
    source = Location.objects.create(organization=org, branch=branch, name="Source POS", code="SRC-POS", location_type="pos")
    category = Category.objects.create(organization=org, name="Phones", code="phones")
    product = Product.objects.create(
        organization=org,
        category=category,
        name="Tracked phone",
        sku=f"PHONE-{action_type}",
        is_serialized=True,
        selling_price=Decimal("20000.00"),
    )
    unit = StockUnit.objects.create(
        organization=org,
        product=product,
        serial_number=f"EXECUTE-{action_type}",
        location=source,
        status=SerialStatus.AVAILABLE,
        unit_cost=Decimal("10000.00"),
    )
    post_stock_movement(
        organization=org,
        product=product,
        location=source,
        quantity=1,
        movement_type=StockMovementType.OPENING,
        actor=manager,
        stock_unit=unit,
    )
    action = request_aged_stock_action(
        stock_unit=unit,
        action_type=action_type,
        reason="Approved aged stock action.",
        proposal=proposal,
        requested_by=manager,
    )
    approval = action.approval
    approval.status = ApprovalStatus.APPROVED
    approval.decided_by = owner
    approval.decided_at = timezone.now()
    approval.save(update_fields=["status", "decided_by", "decided_at", "updated_at"])
    action = sync_aged_stock_action_from_approval(approval=approval)
    return action, manager, owner, source


@pytest.mark.django_db
def test_approved_aged_stock_discount_creates_unit_offer():
    action, _, owner, _ = _approved_aged_action(
        action_type="discount",
        proposal={
            "promotional_price": "18000.00",
            "valid_until": (timezone.localdate() + timedelta(days=14)).isoformat(),
        },
    )

    executed = execute_aged_stock_action(action=action, actor=owner)

    offer = StockUnitOffer.objects.get(aged_stock_action=executed)
    assert executed.status == AgedStockActionStatus.COMPLETED
    assert offer.promotional_price == Decimal("18000.00")
    assert offer.stock_unit == action.stock_unit


@pytest.mark.django_db
def test_approved_aged_stock_writeoff_posts_once_and_removes_available_stock():
    action, _, owner, source = _approved_aged_action(action_type="write_off", proposal={})

    first = execute_aged_stock_action(action=action, actor=owner)
    second = execute_aged_stock_action(action=action, actor=owner)

    first.stock_unit.refresh_from_db()
    assert second.id == first.id
    assert first.status == AgedStockActionStatus.COMPLETED
    assert first.stock_unit.status == SerialStatus.WRITTEN_OFF
    assert first.stock_unit.location is None
    assert StockBalance.objects.get(
        organization=first.organization,
        product=first.stock_unit.product,
        location=source,
    ).quantity == 0
    assert StockMovement.objects.filter(
        organization=first.organization,
        reference_type="aged_stock_action",
        reference_id=str(first.id),
    ).count() == 1


@pytest.mark.django_db
def test_approved_aged_stock_transfer_creates_one_approved_transfer():
    action, _, owner, source = _approved_aged_action(action_type="transfer", proposal={})
    destination = Location.objects.create(
        organization=action.organization,
        branch=source.branch,
        name="Destination POS",
        code="DST-POS",
        location_type="pos",
    )
    action.proposal = {"destination_id": str(destination.id), "destination_name": destination.name}
    action.save(update_fields=["proposal", "updated_at"])

    first = execute_aged_stock_action(action=action, actor=owner)
    second = execute_aged_stock_action(action=action, actor=owner)

    from apps.transfers.models import StockTransfer

    assert first.status == AgedStockActionStatus.APPROVED
    assert second.execution_result == first.execution_result
    transfer = StockTransfer.objects.get(id=first.execution_result["transfer_id"])
    assert transfer.status == "approved"
    assert transfer.source == source
    assert transfer.destination == destination
    assert StockTransfer.objects.filter(notes__contains=str(first.id)).count() == 1

# Create your tests here.
