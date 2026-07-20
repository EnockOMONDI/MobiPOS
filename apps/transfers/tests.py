import pytest
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.inventory.models import SerialStatus, StockBalance, StockUnit
from apps.organizations.models import Location, LocationType, Membership, MembershipStatus, Organization, Role
from apps.transfers.models import StockTransfer, TransferDiscrepancy


@pytest.mark.django_db
def test_transfer_staff_workflow_moves_quantity_stock(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    source = Location.objects.filter(organization=organization, location_type="pos").order_by("code").first()
    destination = Location.objects.filter(organization=organization, location_type="warehouse").order_by("code").first()
    product = Product.objects.get(organization=organization, sku="CHG-20W")
    client.force_login(user)

    response = client.post(reverse("transfer-create"), {
        "source": source.id, "destination": destination.id, "product": product.id,
        "quantity": "3", "notes": "Move stock",
    })
    transfer = StockTransfer.objects.filter(organization=organization).latest("created_at")
    assert response.status_code == 302
    client.post(reverse("transfer-approve", args=[transfer.id]))
    client.post(reverse("transfer-dispatch", args=[transfer.id]))
    client.post(reverse("transfer-receive", args=[transfer.id]))

    transfer.refresh_from_db()
    assert transfer.status == "received"
    assert StockBalance.objects.get(organization=organization, product=product, location=destination).quantity == 3


@pytest.mark.django_db
def test_transfer_create_supports_multiple_quantity_lines(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    source = Location.objects.filter(organization=organization, location_type="pos").order_by("code").first()
    destination = Location.objects.filter(organization=organization, location_type="warehouse").order_by("code").first()
    first = Product.objects.get(organization=organization, sku="CHG-20W")
    second = Product.objects.create(
        organization=organization, category=first.category, brand=first.brand,
        name="Transfer Cable", sku="TRANSFER-CABLE", is_serialized=False,
    )
    products = [first, second]
    client.force_login(user)

    response = client.post(reverse("transfer-create"), {
        "source": source.id, "destination": destination.id,
        "product": products[0].id, "quantity": "2",
        "product_2": products[1].id, "quantity_2": "3",
        "notes": "Multi-line movement",
    })

    transfer = StockTransfer.objects.filter(organization=organization).latest("created_at")
    assert response.status_code == 302
    assert transfer.lines.count() == 2
    assert set(transfer.lines.values_list("product_id", flat=True)) == {product.id for product in products}


@pytest.mark.django_db
def test_transfer_create_selects_existing_serialized_devices(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    first_unit = StockUnit.objects.filter(organization=organization, status=SerialStatus.AVAILABLE).first()
    source = first_unit.location
    units = list(StockUnit.objects.filter(
        organization=organization, status=SerialStatus.AVAILABLE, location=source
    )[:2])
    destination = Location.objects.filter(organization=organization).exclude(id=source.id).first()
    client.force_login(user)

    response = client.post(reverse("transfer-create"), {
        "source": source.id,
        "destination": destination.id,
        "selected_stock_units": [str(unit.id) for unit in units],
        "notes": "Allocate existing devices",
    })

    transfer = StockTransfer.objects.filter(organization=organization).latest("created_at")
    assert response.status_code == 302
    assert transfer.lines.count() == len(units)
    assert set(transfer.lines.values_list("stock_unit_id", flat=True)) == {unit.id for unit in units}


@pytest.mark.django_db
def test_owner_can_allocate_existing_devices_to_agent_custody_in_one_step(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    owner_membership = Membership.objects.get(organization=organization, user=owner)
    branch = owner_membership.branches.first()
    agent = User.objects.create_user(username="allocation-agent", email="allocation-agent@example.com", first_name="Allocation", last_name="Agent")
    agent_membership = Membership.objects.create(
        organization=organization,
        user=agent,
        status=MembershipStatus.ACTIVE,
    )
    agent_membership.branches.add(branch)
    agent_location = Location.objects.create(
        organization=organization,
        branch=branch,
        name="Allocation Agent Stock",
        code="ALLOC-AGENT",
        location_type=LocationType.AGENT,
        custodian_membership=agent_membership,
    )
    first_unit = StockUnit.objects.filter(organization=organization, status=SerialStatus.AVAILABLE).exclude(location__location_type=LocationType.AGENT).first()
    source = first_unit.location
    units = list(StockUnit.objects.filter(
        organization=organization,
        status=SerialStatus.AVAILABLE,
        location=source,
    )[:2])
    client.force_login(owner)

    response = client.post(reverse("agent-allocation-create"), {
        "source": source.id,
        "agent_location": agent_location.id,
        "selected_stock_units": [str(unit.id) for unit in units],
        "complete_now": "on",
        "notes": "Issue phones to field agent",
    })

    transfer = StockTransfer.objects.filter(organization=organization, number__startswith="AGT-").latest("created_at")
    assert response.status_code == 302
    assert transfer.status == "received"
    assert transfer.destination == agent_location
    assert transfer.lines.count() == len(units)
    assert set(transfer.lines.values_list("stock_unit_id", flat=True)) == {unit.id for unit in units}
    assert set(StockUnit.objects.filter(id__in=[unit.id for unit in units]).values_list("location_id", flat=True)) == {agent_location.id}


@pytest.mark.django_db
def test_owner_can_recall_agent_devices_in_one_step(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    owner_membership = Membership.objects.get(organization=organization, user=owner)
    branch = owner_membership.branches.first()
    agent = User.objects.create_user(username="recall-agent", email="recall-agent@example.com", first_name="Recall", last_name="Agent")
    agent_membership = Membership.objects.create(
        organization=organization,
        user=agent,
        status=MembershipStatus.ACTIVE,
    )
    agent_membership.branches.add(branch)
    agent_location = Location.objects.create(
        organization=organization,
        branch=branch,
        name="Recall Agent Stock",
        code="RECALL-AGENT",
        location_type=LocationType.AGENT,
        custodian_membership=agent_membership,
    )
    unit = StockUnit.objects.filter(
        organization=organization,
        status=SerialStatus.AVAILABLE,
    ).exclude(location__location_type=LocationType.AGENT).first()
    source = unit.location
    destination = Location.objects.filter(
        organization=organization,
    ).exclude(id=source.id).exclude(location_type=LocationType.AGENT).first()
    client.force_login(owner)
    client.post(reverse("agent-allocation-create"), {
        "source": source.id,
        "agent_location": agent_location.id,
        "selected_stock_units": [str(unit.id)],
        "complete_now": "on",
    })
    unit.refresh_from_db()
    assert unit.location == agent_location

    response = client.post(reverse("agent-recall-create"), {
        "agent_location": agent_location.id,
        "destination": destination.id,
        "selected_stock_units": [str(unit.id)],
        "complete_now": "on",
        "notes": "Recall for reassignment",
    })

    transfer = StockTransfer.objects.filter(organization=organization, number__startswith="RCL-").latest("created_at")
    unit.refresh_from_db()
    assert response.status_code == 302
    assert transfer.status == "received"
    assert transfer.source == agent_location
    assert transfer.destination == destination
    assert unit.location == destination
    assert unit.status == SerialStatus.AVAILABLE


@pytest.mark.django_db
def test_staff_without_transfer_change_permission_can_only_request_agent_allocation(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    owner_membership = Membership.objects.get(organization=organization, user=owner)
    branch = owner_membership.branches.first()
    staff = User.objects.create_user(username="allocation-staff", email="allocation-staff@example.com")
    staff_membership = Membership.objects.create(
        organization=organization,
        user=staff,
        status=MembershipStatus.ACTIVE,
    )
    staff_membership.branches.add(branch)
    request_role = Role.objects.create(organization=organization, name="Allocation requester", code="allocation-requester")
    request_role.permissions.add(Permission.objects.get(content_type__app_label="transfers", codename="add_stocktransfer"))
    staff_membership.roles.add(request_role)
    agent = User.objects.create_user(username="request-agent", email="request-agent@example.com")
    agent_membership = Membership.objects.create(
        organization=organization,
        user=agent,
        status=MembershipStatus.ACTIVE,
    )
    agent_membership.branches.add(branch)
    agent_location = Location.objects.create(
        organization=organization,
        branch=branch,
        name="Request Agent Stock",
        code="REQ-AGENT",
        location_type=LocationType.AGENT,
        custodian_membership=agent_membership,
    )
    unit = StockUnit.objects.filter(
        organization=organization,
        status=SerialStatus.AVAILABLE,
        location__branch=branch,
    ).exclude(location__location_type=LocationType.AGENT).first()
    client.force_login(staff)

    response = client.post(reverse("agent-allocation-create"), {
        "source": unit.location_id,
        "agent_location": agent_location.id,
        "selected_stock_units": [str(unit.id)],
        "complete_now": "on",
    })

    transfer = StockTransfer.objects.filter(organization=organization, number__startswith="AGT-").latest("created_at")
    unit.refresh_from_db()
    assert response.status_code == 302
    assert transfer.status == "requested"
    assert unit.location_id != agent_location.id


@pytest.mark.django_db
def test_device_search_returns_authorized_available_devices(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    unit = StockUnit.objects.filter(organization=organization, status=SerialStatus.AVAILABLE).select_related("location").first()
    client.force_login(user)

    response = client.get(reverse("device-search"), {"q": unit.serial_number[:6], "source": unit.location_id})

    assert response.status_code == 200
    payload = response.json()
    assert any(item["id"] == str(unit.id) for item in payload["results"])


@pytest.mark.django_db
def test_non_owner_cannot_approve_transfer(client):
    call_command("seed_demo_data")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    staff = User.objects.create_user(username="transfer-staff", email="transfer-staff@example.com")
    Membership.objects.create(organization=organization, user=staff, status=MembershipStatus.ACTIVE)
    locations = list(Location.objects.filter(organization=organization)[:2])
    transfer = StockTransfer.objects.create(
        organization=organization, number="TRF-AUTH", source=locations[0],
        destination=locations[1], requested_by=staff,
    )
    client.force_login(staff)

    response = client.post(reverse("transfer-approve", args=[transfer.id]))

    assert response.status_code == 403


@pytest.mark.django_db
def test_transfer_discrepancy_can_be_received_and_reconciled(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    source = Location.objects.filter(organization=organization, location_type="pos").order_by("code").first()
    destination = Location.objects.filter(organization=organization, location_type="warehouse").order_by("code").first()
    product = Product.objects.get(organization=organization, sku="CHG-20W")
    client.force_login(user)
    client.post(reverse("transfer-create"), {
        "source": source.id, "destination": destination.id, "product": product.id, "quantity": "3",
    })
    transfer = StockTransfer.objects.filter(organization=organization).latest("created_at")
    client.post(reverse("transfer-approve", args=[transfer.id]))
    client.post(reverse("transfer-dispatch", args=[transfer.id]))
    line = transfer.lines.get()
    client.post(reverse("transfer-receive", args=[transfer.id]), {
        f"received_{line.id}": "2", "discrepancy_reason": "One item missing",
    })

    transfer.refresh_from_db()
    discrepancy = TransferDiscrepancy.objects.get(transfer=transfer)
    assert transfer.status == "discrepancy"
    assert discrepancy.difference == 1

    client.post(reverse("transfer-discrepancy-resolve", args=[discrepancy.id]), {"resolution": "receive"})
    transfer.refresh_from_db()
    discrepancy.refresh_from_db()
    assert transfer.status == "received"
    assert discrepancy.status == "resolved"
    assert StockBalance.objects.get(organization=organization, product=product, location=destination).quantity == 3
