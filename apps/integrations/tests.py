import pytest
from django.urls import reverse
from django.test import override_settings
from django.utils import timezone
from unittest.mock import patch

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.catalog.models import Category, Product
from apps.contacts.models import Contact
from apps.integrations.models import (
    FiscalDevice,
    FiscalDeviceStatus,
    FiscalDocument,
    FiscalDocumentStatus,
    FiscalDocumentType,
    IntegrationEvent,
    IntegrationStatus,
)
from apps.integrations.services import create_return_fiscal_document, create_sale_fiscal_document, queue_integration_event
from apps.integrations.tasks import process_due_integration_events, process_integration_event
from apps.organizations.models import Branch, Company, Location, Membership, MembershipStatus, Organization
from apps.payments.models import Payment, PaymentMethod, PaymentStatus
from apps.pos.models import POSSession
from apps.sales.models import Sale, SaleLine, SaleReturn, SaleReturnLine


def _retail_fixture(slug="etims-retail"):
    user = User.objects.create_user(username=f"{slug}-owner", email=f"{slug}@example.com")
    org = Organization.objects.create(name="eTIMS Retail", slug=slug, status="active")
    company = Company.objects.create(organization=org, name="eTIMS Retail Ltd", code="ET", tax_number="P051234568B")
    branch = Branch.objects.create(organization=org, company=company, name="Nairobi CBD", code="CBD")
    location = Location.objects.create(organization=org, branch=branch, name="CBD POS", code="CBDPOS", location_type="pos")
    membership = Membership.objects.create(organization=org, user=user, status=MembershipStatus.ACTIVE, is_owner=True)
    membership.branches.add(branch)
    session = POSSession.objects.create(organization=org, number=f"SES-{slug}", location=location, cashier=user)
    category = Category.objects.create(organization=org, name="Phones", code=f"phones-{slug}")
    product = Product.objects.create(
        organization=org,
        category=category,
        name="Samsung A07",
        sku=f"A07-{slug}",
        selling_price=10000,
        cost_price=7000,
        tax_rate=16,
        is_serialized=True,
    )
    customer = Contact.objects.create(
        organization=org,
        contact_type="customer",
        name="Brian Demo",
        phone_number="0712345678",
        tax_number="A123456789B",
    )
    sale = Sale.objects.create(
        organization=org,
        number=f"SALE-{slug}",
        session=session,
        location=location,
        customer=customer,
        created_by=user,
        completed_at=timezone.now(),
        subtotal=10000,
        tax_total=1600,
        total=11600,
        paid_total=11600,
        status="paid",
    )
    SaleLine.objects.create(
        organization=org,
        sale=sale,
        product=product,
        quantity=1,
        unit_price=10000,
        unit_cost=7000,
        tax=1600,
        line_total=10000,
    )
    Payment.objects.create(
        organization=org,
        number=f"PAY-{slug}",
        customer=customer,
        sale=sale,
        method=PaymentMethod.CASH,
        status=PaymentStatus.CONFIRMED,
        amount=11600,
        received_by=user,
    )
    return user, org, company, branch, location, sale


@pytest.mark.django_db
def test_integration_outbox_is_idempotent():
    org = Organization.objects.create(name="Org", slug="integration-org")
    first = queue_integration_event(organization=org, provider="etims", event_type="invoice", idempotency_key="same-key", payload={"number": "1"})
    second = queue_integration_event(organization=org, provider="etims", event_type="invoice", idempotency_key="same-key", payload={"number": "1"})

    assert first == second
    assert IntegrationEvent.objects.count() == 1


@pytest.mark.django_db
def test_due_integration_events_are_dispatched():
    org = Organization.objects.create(name="Retry Org", slug="retry-org")
    event = IntegrationEvent.objects.create(
        organization=org, provider="etims", event_type="invoice",
        idempotency_key="retry-key", payload={}, status=IntegrationStatus.FAILED,
        next_retry_at=timezone.now(),
    )
    with patch("apps.integrations.tasks.process_integration_event.delay") as delay:
        assert process_due_integration_events() == 1
        delay.assert_called_once_with(str(event.id))


@pytest.mark.django_db
def test_unknown_provider_event_is_marked_failed():
    org = Organization.objects.create(name="Unknown Org", slug="unknown-org")
    event = IntegrationEvent.objects.create(
        organization=org, provider="unknown", event_type="invoice",
        idempotency_key="unknown-key", payload={},
    )

    process_integration_event(str(event.id))
    event.refresh_from_db()

    assert event.status == IntegrationStatus.FAILED
    assert event.attempts == 1


@pytest.mark.django_db
@override_settings(INTEGRATION_MODE="disabled")
def test_placeholder_adapter_cannot_succeed_when_integrations_are_disabled():
    org = Organization.objects.create(name="Disabled Org", slug="disabled-org")
    event = IntegrationEvent.objects.create(
        organization=org, provider="etims", event_type="invoice",
        idempotency_key="disabled-key", payload={},
    )

    process_integration_event(str(event.id))
    event.refresh_from_db()

    assert event.status == IntegrationStatus.FAILED
    assert "not activated" in event.error_message


@pytest.mark.django_db
def test_sale_without_active_etims_device_keeps_standard_receipt_only():
    _user, _org, _company, _branch, _location, sale = _retail_fixture("standard-only")

    document = create_sale_fiscal_document(sale=sale)

    assert document is None
    assert FiscalDocument.objects.count() == 0
    assert IntegrationEvent.objects.filter(provider="etims").count() == 0


@pytest.mark.django_db
def test_active_sandbox_device_creates_idempotent_sale_fiscal_document_and_outbox():
    _user, org, company, _branch, location, sale = _retail_fixture("fiscal-sale")
    FiscalDevice.objects.create(
        organization=org,
        company=company,
        location=location,
        status=FiscalDeviceStatus.SANDBOX_TESTING,
        taxpayer_pin="P051234568B",
        branch_office_id="00",
    )

    first = create_sale_fiscal_document(sale=sale)
    second = create_sale_fiscal_document(sale=sale)

    assert first == second
    assert first.document_type == FiscalDocumentType.SALE
    assert first.status == FiscalDocumentStatus.PENDING
    assert first.lines.count() == 1
    assert first.payload_snapshot["taxpayer_pin"] == "P051234568B"
    assert first.payload_snapshot["buyer"]["pin"] == "A123456789B"
    assert IntegrationEvent.objects.filter(
        provider="etims",
        event_type="invoice.submit",
        idempotency_key=f"etims-sale-{sale.id}",
    ).count() == 1


@pytest.mark.django_db
@override_settings(INTEGRATION_MODE="sandbox")
def test_sandbox_etims_event_updates_fiscal_document_status():
    _user, org, company, _branch, location, sale = _retail_fixture("fiscal-accepted")
    FiscalDevice.objects.create(
        organization=org,
        company=company,
        location=location,
        status=FiscalDeviceStatus.SANDBOX_TESTING,
        taxpayer_pin="P051234568B",
        branch_office_id="00",
    )
    document = create_sale_fiscal_document(sale=sale)
    event = IntegrationEvent.objects.get(idempotency_key=f"etims-sale-{sale.id}")

    process_integration_event(str(event.id))
    document.refresh_from_db()

    assert document.status == FiscalDocumentStatus.ACCEPTED
    assert document.etims_invoice_number.startswith("SBX-")
    assert document.etims_control_code.startswith("CTRL-")
    assert document.qr_payload


@pytest.mark.django_db
def test_return_creates_etims_credit_note_document():
    user, org, company, _branch, location, sale = _retail_fixture("fiscal-return")
    FiscalDevice.objects.create(
        organization=org,
        company=company,
        location=location,
        status=FiscalDeviceStatus.SANDBOX_TESTING,
        taxpayer_pin="P051234568B",
        branch_office_id="00",
    )
    sale_document = create_sale_fiscal_document(sale=sale)
    sale_document.etims_invoice_number = "KRA-SALE-001"
    sale_document.status = FiscalDocumentStatus.ACCEPTED
    sale_document.save(update_fields=["etims_invoice_number", "status", "updated_at"])
    sale_return = SaleReturn.objects.create(
        organization=org,
        number="RET-fiscal-return",
        sale=sale,
        status="completed",
        reason="Customer return",
        refund_amount=11600,
        requested_by=user,
        approved_by=user,
    )
    SaleReturnLine.objects.create(
        organization=org,
        sale_return=sale_return,
        sale_line=sale.lines.first(),
        quantity=1,
        refundable_amount=11600,
    )

    credit_note = create_return_fiscal_document(sale_return=sale_return)

    assert credit_note.document_type == FiscalDocumentType.CREDIT_NOTE
    assert credit_note.payload_snapshot["original_etims_invoice_number"] == "KRA-SALE-001"
    assert credit_note.lines.count() == 1
    assert IntegrationEvent.objects.filter(
        provider="etims",
        event_type="credit_note.submit",
        idempotency_key=f"etims-return-{sale_return.id}",
    ).exists()


@pytest.mark.django_db
def test_owner_can_configure_etims_device_from_settings_page(client):
    user, org, company, _branch, location, _sale = _retail_fixture("settings")
    client.force_login(user)

    response = client.post(reverse("etims-settings"), {
        "environment": "sandbox",
        "mode": "oscu",
        "status": FiscalDeviceStatus.SANDBOX_TESTING,
        "receipt_policy": "standard_and_etims",
        "company": str(company.id),
        "location": str(location.id),
        "taxpayer_pin": "p051234568b",
        "branch_office_id": "00",
        "device_serial": "SBX-001",
        "device_name": "Main sandbox device",
    })

    device = FiscalDevice.objects.get(organization=org)
    assert response.status_code == 302
    assert device.taxpayer_pin == "P051234568B"
    assert device.location == location
    assert AuditEvent.objects.filter(
        organization=org,
        actor=user,
        action="integrations.etims_device_configured",
    ).exists()


@pytest.mark.django_db
def test_sale_detail_shows_accepted_etims_tax_receipt(client):
    user, org, company, _branch, location, sale = _retail_fixture("receipt-ui")
    FiscalDevice.objects.create(
        organization=org,
        company=company,
        location=location,
        status=FiscalDeviceStatus.SANDBOX_TESTING,
        taxpayer_pin="P051234568B",
        branch_office_id="00",
    )
    document = create_sale_fiscal_document(sale=sale)
    document.status = FiscalDocumentStatus.ACCEPTED
    document.etims_invoice_number = "SBX-RECEIPT-001"
    document.etims_control_code = "CTRL-001"
    document.qr_payload = "ETIMS|SBX-RECEIPT-001"
    document.save(update_fields=[
        "status",
        "etims_invoice_number",
        "etims_control_code",
        "qr_payload",
        "updated_at",
    ])
    client.force_login(user)

    response = client.get(reverse("sale-detail", args=[sale.id]))

    assert response.status_code == 200
    assert b"eTIMS tax receipt" in response.content
    assert b"SBX-RECEIPT-001" in response.content
    assert b"CTRL-001" in response.content
