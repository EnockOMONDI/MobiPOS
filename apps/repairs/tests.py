import uuid

import pytest
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.contacts.models import Contact
from apps.catalog.models import Product
from apps.inventory.models import StockBalance, StockMovement, StockUnit
from apps.organizations.models import Branch, Location, Organization
from apps.repairs.forms import RepairTicketForm
from apps.repairs.models import RepairPartUsage, RepairPayment, RepairStatus, RepairTicket, RepairTransition
from apps.repairs.services import transition_repair
from apps.sales.models import SaleLine


@pytest.fixture
def repair_context(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    branch = Branch.objects.filter(organization=organization).order_by("code").first()
    customer = Contact.objects.get(organization=organization, name="Demo Credit Customer")
    client.force_login(user)
    response = client.post(reverse("repair-create"), {
        "branch": branch.id,
        "customer": customer.id,
        "issue": "Broken screen",
        "quoted_amount": "5000",
    })
    ticket = RepairTicket.objects.get(organization=organization, issue="Broken screen")
    assert response.status_code == 302
    return client, user, organization, branch, ticket


def transition(client, ticket, status, *, diagnosis="Screen assembly is damaged.", notes="Customer approved the repair."):
    return client.post(reverse("repair-update", args=[ticket.id]), {
        "request_id": uuid.uuid4(),
        "status": status,
        "diagnosis": diagnosis,
        "warranty_type": "none",
        "warranty_decision_notes": "",
        "quoted_amount": "5000",
        "transition_notes": notes,
    }, follow=True)


@pytest.mark.django_db
def test_repair_lifecycle_payment_and_collection(repair_context):
    client, _, _, _, ticket = repair_context

    transition(client, ticket, RepairStatus.DIAGNOSING)
    transition(client, ticket, RepairStatus.AWAITING_APPROVAL)
    transition(client, ticket, RepairStatus.IN_REPAIR)
    transition(client, ticket, RepairStatus.QUALITY_CHECK, notes="Repair work completed.")
    transition(client, ticket, RepairStatus.READY, notes="Device passed functional checks.")

    unpaid_response = transition(client, ticket, RepairStatus.CLOSED, notes="Customer collected device.")
    ticket.refresh_from_db()
    assert ticket.status == RepairStatus.READY
    assert b"outstanding balance" in unpaid_response.content

    first_request_id = uuid.uuid4()
    payment_payload = {
        "request_id": first_request_id,
        "amount": "2000",
        "method": "mpesa",
        "reference": "QHX123PAY",
        "notes": "First instalment",
    }
    assert client.post(reverse("repair-add-payment", args=[ticket.id]), payment_payload).status_code == 302
    assert client.post(reverse("repair-add-payment", args=[ticket.id]), payment_payload).status_code == 302
    assert RepairPayment.objects.filter(ticket=ticket, request_id=first_request_id).count() == 1

    client.post(reverse("repair-add-payment", args=[ticket.id]), {
        "request_id": uuid.uuid4(),
        "amount": "3000",
        "method": "cash",
        "reference": "",
        "notes": "Balance",
    })
    transition(client, ticket, RepairStatus.CLOSED, notes="Identity and device handover confirmed.")
    ticket.refresh_from_db()
    assert ticket.status == RepairStatus.CLOSED
    assert ticket.collected_at is not None
    assert ticket.amount_paid == 5000
    assert ticket.outstanding_amount == 0
    assert RepairTransition.objects.filter(ticket=ticket).count() == 6


@pytest.mark.django_db
def test_invalid_transition_and_part_issue_outside_repair_are_rejected(repair_context):
    client, _, organization, _, ticket = repair_context
    invalid = transition(client, ticket, RepairStatus.CLOSED)
    ticket.refresh_from_db()
    assert ticket.status == RepairStatus.RECEIVED
    assert b"Select one of the valid next stages" in invalid.content

    part = Product.objects.get(organization=organization, sku="CHG-20W")
    location = Location.objects.filter(organization=organization, location_type="pos").order_by("code").first()
    before = StockBalance.objects.get(organization=organization, product=part, location=location).quantity
    rejected = client.post(reverse("repair-use-part", args=[ticket.id]), {
        "request_id": uuid.uuid4(),
        "product": part.id,
        "location": location.id,
        "quantity": "1",
    }, follow=True)
    assert b"Parts can only be issued" in rejected.content
    assert not RepairPartUsage.objects.filter(ticket=ticket, product=part).exists()
    assert StockBalance.objects.get(organization=organization, product=part, location=location).quantity == before


@pytest.mark.django_db
def test_part_issue_replay_and_owner_reversal_restore_stock(repair_context):
    client, _, organization, _, ticket = repair_context
    transition(client, ticket, RepairStatus.DIAGNOSING)
    transition(client, ticket, RepairStatus.AWAITING_APPROVAL)
    transition(client, ticket, RepairStatus.IN_REPAIR)

    part = Product.objects.get(organization=organization, sku="CHG-20W")
    location = Location.objects.filter(organization=organization, location_type="pos").order_by("code").first()
    before = StockBalance.objects.get(organization=organization, product=part, location=location).quantity
    request_id = uuid.uuid4()
    payload = {
        "request_id": request_id,
        "product": part.id,
        "location": location.id,
        "quantity": "1",
    }
    client.post(reverse("repair-use-part", args=[ticket.id]), payload)
    client.post(reverse("repair-use-part", args=[ticket.id]), payload)
    usage = RepairPartUsage.objects.get(ticket=ticket, request_id=request_id)
    assert StockBalance.objects.get(organization=organization, product=part, location=location).quantity == before - 1
    assert StockMovement.objects.filter(reference_type="repair_ticket", reference_id=str(ticket.id)).count() == 1

    reversed_response = client.post(
        reverse("repair-reverse-part", args=[ticket.id, usage.id]),
        {"reason": "Part was not required after diagnosis review."},
        follow=True,
    )
    usage.refresh_from_db()
    assert b"stock restored" in reversed_response.content
    assert usage.reversed_at is not None
    assert usage.reversal_movement_id is not None
    assert StockBalance.objects.get(organization=organization, product=part, location=location).quantity == before


@pytest.mark.django_db
def test_serialized_part_reversal_restores_unit(repair_context):
    client, _, organization, branch, ticket = repair_context
    transition(client, ticket, RepairStatus.DIAGNOSING)
    transition(client, ticket, RepairStatus.AWAITING_APPROVAL)
    transition(client, ticket, RepairStatus.IN_REPAIR)

    unit = StockUnit.objects.filter(
        organization=organization,
        status="available",
        location__branch=branch,
    ).select_related("product", "location").first()
    assert unit is not None
    original_location = unit.location
    response = client.post(reverse("repair-use-part", args=[ticket.id]), {
        "request_id": uuid.uuid4(),
        "product": unit.product_id,
        "stock_unit": unit.id,
        "location": original_location.id,
        "quantity": "1",
    })
    assert response.status_code == 302
    usage = RepairPartUsage.objects.get(ticket=ticket, stock_unit=unit)
    unit.refresh_from_db()
    assert unit.status == "warranty_repair"
    assert unit.location_id is None

    client.post(
        reverse("repair-reverse-part", args=[ticket.id, usage.id]),
        {"reason": "Serialized spare was selected in error."},
    )
    unit.refresh_from_db()
    assert unit.status == "available"
    assert unit.location_id == original_location.id


@pytest.mark.django_db
def test_payment_validation_reversal_and_immutability(repair_context):
    client, _, _, _, ticket = repair_context
    missing_reference = client.post(reverse("repair-add-payment", args=[ticket.id]), {
        "request_id": uuid.uuid4(),
        "amount": "500",
        "method": "mpesa",
        "reference": "",
    }, follow=True)
    assert b"Correct the repair payment details" in missing_reference.content

    overpayment = client.post(reverse("repair-add-payment", args=[ticket.id]), {
        "request_id": uuid.uuid4(),
        "amount": "6000",
        "method": "cash",
        "reference": "",
    }, follow=True)
    assert b"exceeds the outstanding repair balance" in overpayment.content

    client.post(reverse("repair-add-payment", args=[ticket.id]), {
        "request_id": uuid.uuid4(),
        "amount": "1000",
        "method": "bank",
        "reference": "BANK-REPAIR-1",
    })
    payment = RepairPayment.objects.get(ticket=ticket)
    payment.notes = "Edited"
    with pytest.raises(ValidationError):
        payment.save()
    with pytest.raises(ValidationError):
        payment.delete()

    response = client.post(
        reverse("repair-reverse-payment", args=[ticket.id, payment.id]),
        {"reason": "Bank transfer was reversed by the bank."},
        follow=True,
    )
    payment.refresh_from_db()
    assert b"reversed" in response.content
    assert payment.reversed_at is not None
    assert ticket.amount_paid == 0


@pytest.mark.django_db
def test_repair_routes_are_tenant_scoped(repair_context):
    client, _, _, _, _ = repair_context
    assert client.get(reverse("repair-detail", args=[uuid.uuid4()])).status_code == 404
    assert client.post(reverse("repair-add-payment", args=[uuid.uuid4()]), {}).status_code == 404


@pytest.mark.django_db
def test_transition_service_is_replay_safe(repair_context):
    _, user, _, _, ticket = repair_context
    request_id = uuid.uuid4()
    first = transition_repair(
        ticket=ticket,
        to_status=RepairStatus.DIAGNOSING,
        actor=user,
        request_id=request_id,
        diagnosis="",
        warranty_type="none",
        quoted_amount="5000",
    )
    replay = transition_repair(
        ticket=ticket,
        to_status=RepairStatus.DIAGNOSING,
        actor=user,
        request_id=request_id,
        diagnosis="",
        warranty_type="none",
        quoted_amount="5000",
    )
    assert replay.id == first.id
    assert RepairTransition.objects.filter(ticket=ticket).count() == 1


@pytest.mark.django_db
def test_approved_quote_and_warranty_cannot_change_during_collection(repair_context):
    client, _, _, _, ticket = repair_context
    transition(client, ticket, RepairStatus.DIAGNOSING)
    transition(client, ticket, RepairStatus.AWAITING_APPROVAL)
    transition(client, ticket, RepairStatus.IN_REPAIR)
    transition(client, ticket, RepairStatus.QUALITY_CHECK, notes="Repair work completed.")
    transition(client, ticket, RepairStatus.READY, notes="Device passed functional checks.")

    response = client.post(reverse("repair-update", args=[ticket.id]), {
        "request_id": uuid.uuid4(),
        "status": RepairStatus.CLOSED,
        "diagnosis": "Screen assembly is damaged.",
        "warranty_type": "internal",
        "warranty_decision_notes": "",
        "quoted_amount": "0",
        "transition_notes": "Attempted collection bypass.",
    }, follow=True)
    ticket.refresh_from_db()
    assert ticket.status == RepairStatus.READY
    assert ticket.warranty_type == "none"
    assert ticket.quoted_amount == 5000
    assert b"approved warranty decision and quote cannot change" in response.content


@pytest.mark.django_db
def test_repair_intake_only_accepts_device_sold_to_selected_customer(repair_context):
    _, user, organization, branch, _ = repair_context
    sold_line = SaleLine.objects.filter(
        organization=organization,
        stock_unit__isnull=False,
        sale__customer__isnull=False,
        sale__status__in=("completed", "part_paid", "paid", "returned"),
    ).select_related("sale__customer", "stock_unit").first()
    assert sold_line is not None

    valid_form = RepairTicketForm(data={
        "branch": branch.id,
        "customer": sold_line.sale.customer_id,
        "stock_unit": sold_line.stock_unit_id,
        "issue": "Device no longer charges.",
        "warranty_type": "customer",
        "quoted_amount": "0",
    }, organization=organization, user=user)
    assert valid_form.is_valid(), valid_form.errors

    other_customer = Contact.objects.filter(
        organization=organization,
        contact_type__in=("customer", "both"),
    ).exclude(id=sold_line.sale.customer_id).first()
    assert other_customer is not None
    invalid_form = RepairTicketForm(data={
        "branch": branch.id,
        "customer": other_customer.id,
        "stock_unit": sold_line.stock_unit_id,
        "issue": "Device no longer charges.",
        "warranty_type": "customer",
        "quoted_amount": "0",
    }, organization=organization, user=user)
    assert not invalid_form.is_valid()
    assert "Select a device previously sold to this customer." in invalid_form.errors["stock_unit"]


@pytest.mark.django_db
def test_duplicate_repair_payment_reference_is_rejected(repair_context):
    client, _, _, _, ticket = repair_context
    client.post(reverse("repair-add-payment", args=[ticket.id]), {
        "request_id": uuid.uuid4(),
        "amount": "1000",
        "method": "mpesa",
        "reference": "REPAIR-MPESA-001",
    })
    response = client.post(reverse("repair-add-payment", args=[ticket.id]), {
        "request_id": uuid.uuid4(),
        "amount": "500",
        "method": "mpesa",
        "reference": "REPAIR-MPESA-001",
    }, follow=True)
    assert RepairPayment.objects.filter(ticket=ticket).count() == 1
    assert b"payment reference has already been recorded" in response.content


@pytest.mark.django_db
def test_non_owner_cannot_reverse_repair_evidence(repair_context):
    client, _, organization, _, ticket = repair_context
    client.post(reverse("repair-add-payment", args=[ticket.id]), {
        "request_id": uuid.uuid4(),
        "amount": "1000",
        "method": "cash",
        "reference": "",
    })
    payment = RepairPayment.objects.get(ticket=ticket)
    manager = User.objects.get(username=f"{organization.slug}-manager")
    assert manager.memberships.filter(organization=organization, is_owner=False).exists()
    client.force_login(manager)
    response = client.post(
        reverse("repair-reverse-payment", args=[ticket.id, payment.id]),
        {"reason": "This should require owner authority."},
    )
    assert response.status_code == 403
    payment.refresh_from_db()
    assert payment.reversed_at is None
