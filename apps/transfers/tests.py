import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.inventory.models import StockBalance
from apps.organizations.models import Location, Membership, MembershipStatus, Organization
from apps.transfers.models import StockTransfer, TransferDiscrepancy


@pytest.mark.django_db
def test_transfer_staff_workflow_moves_quantity_stock(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="kipekee-electronics")
    source = Location.objects.get(organization=organization, location_type="pos")
    destination = Location.objects.get(organization=organization, location_type="warehouse")
    product = Product.objects.get(organization=organization, sku="CHG-20W")
    client.force_login(user)

    response = client.post(reverse("transfer-create"), {
        "source": source.id, "destination": destination.id, "product": product.id,
        "quantity": "3", "notes": "Move stock",
    })
    transfer = StockTransfer.objects.get(organization=organization)
    assert response.status_code == 302
    client.post(reverse("transfer-approve", args=[transfer.id]))
    client.post(reverse("transfer-dispatch", args=[transfer.id]))
    client.post(reverse("transfer-receive", args=[transfer.id]))

    transfer.refresh_from_db()
    assert transfer.status == "received"
    assert StockBalance.objects.get(organization=organization, product=product, location=destination).quantity == 3


@pytest.mark.django_db
def test_non_owner_cannot_approve_transfer(client):
    call_command("seed_demo_data")
    organization = Organization.objects.get(slug="kipekee-electronics")
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
    user = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="kipekee-electronics")
    source = Location.objects.get(organization=organization, location_type="pos")
    destination = Location.objects.get(organization=organization, location_type="warehouse")
    product = Product.objects.get(organization=organization, sku="CHG-20W")
    client.force_login(user)
    client.post(reverse("transfer-create"), {
        "source": source.id, "destination": destination.id, "product": product.id, "quantity": "3",
    })
    transfer = StockTransfer.objects.get(organization=organization)
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
