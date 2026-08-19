import pytest
from django.core.exceptions import ValidationError

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.catalog.models import Category, Product
from apps.contacts.models import Contact
from apps.organizations.models import Branch, Company, Location, Organization
from apps.operations.models import Receivable
from apps.payments.models import Payment, PaymentStatus, PaymentVerificationSource, Refund
from apps.payments.services import confirm_payment, confirm_refund, record_sale_payments
from apps.pos.models import POSSession
from apps.sales.models import Sale, SaleStatus


@pytest.mark.django_db
def test_confirm_payment_is_idempotent():
    user = User.objects.create_user(username="cashier", email="cashier@example.com")
    org = Organization.objects.create(name="Org", slug="payment-org", status="active")
    company = Company.objects.create(organization=org, name="Company", code="CO")
    branch = Branch.objects.create(organization=org, company=company, name="Branch", code="BR")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="POS", location_type="pos")
    customer = Contact.objects.create(organization=org, contact_type="customer", name="Customer")
    session = POSSession.objects.create(organization=org, number="S1", location=location, cashier=user)
    sale = Sale.objects.create(organization=org, number="SALE1", session=session, location=location, customer=customer, total=1000, created_by=user)
    payment = Payment.objects.create(organization=org, number="PAY1", customer=customer, sale=sale, method="cash", amount=1000, received_by=user)

    confirm_payment(payment=payment)
    confirm_payment(payment=payment)

    sale.refresh_from_db()
    assert sale.paid_total == 1000
    assert sale.status == SaleStatus.PAID
    assert Payment.objects.get(pk=payment.pk).status == PaymentStatus.CONFIRMED


@pytest.mark.django_db
def test_confirm_payment_reconciles_receivable_balance():
    user = User.objects.create_user(username="collector", email="collector@example.com")
    org = Organization.objects.create(name="Org", slug="receivable-payment-org", status="active")
    company = Company.objects.create(organization=org, name="Company", code="CO")
    branch = Branch.objects.create(organization=org, company=company, name="Branch", code="BR")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="POS", location_type="pos")
    customer = Contact.objects.create(organization=org, contact_type="customer", name="Customer")
    session = POSSession.objects.create(organization=org, number="S1", location=location, cashier=user)
    sale = Sale.objects.create(
        organization=org, number="SALE1", session=session, location=location,
        customer=customer, total=1000, created_by=user,
    )
    receivable = Receivable.objects.create(
        organization=org, customer=customer, sale=sale,
        original_amount=1000, outstanding_amount=1000, due_on="2026-07-01",
    )
    from apps.operations.models import ReceivableInstallment
    installment = ReceivableInstallment.objects.create(
        organization=org, receivable=receivable, sequence=1,
        amount=1000, due_on="2026-07-01",
    )
    payment = Payment.objects.create(
        organization=org, number="PAY1", customer=customer, sale=sale,
        method="cash", amount=400, received_by=user,
    )

    confirm_payment(payment=payment)

    receivable.refresh_from_db()
    installment.refresh_from_db()
    assert receivable.outstanding_amount == 600
    assert installment.paid_amount == 400
    assert installment.status == "part_paid"


@pytest.mark.django_db
def test_non_cash_sale_payment_is_manually_verified_for_pilot_workflow():
    user = User.objects.create_user(username="mpesa-cashier", email="mpesa@example.com")
    org = Organization.objects.create(name="Org", slug="mpesa-payment-org", status="active")
    company = Company.objects.create(organization=org, name="Company", code="CO")
    branch = Branch.objects.create(organization=org, company=company, name="Branch", code="BR")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="POS", location_type="pos")
    customer = Contact.objects.create(organization=org, contact_type="customer", name="Customer")
    session = POSSession.objects.create(organization=org, number="S1", location=location, cashier=user)
    sale = Sale.objects.create(
        organization=org, number="SALE1", session=session, location=location,
        customer=customer, total=1000, created_by=user,
    )

    payments = record_sale_payments(
        sale=sale,
        allocations=[{"method": "mpesa", "amount": 1000, "provider_reference": "MPESA123"}],
        received_by=user,
    )

    sale.refresh_from_db()
    assert Payment.objects.get(pk=payments[0].pk).status == PaymentStatus.CONFIRMED
    assert Payment.objects.get(pk=payments[0].pk).verification_source == PaymentVerificationSource.MANUAL
    event = AuditEvent.objects.get(action="payment.manually_verified", target_id=str(payments[0].pk))
    assert event.actor == user
    assert event.metadata["reference"] == "MPESA123"
    sale.refresh_from_db()
    assert sale.paid_total == 1000


@pytest.mark.django_db
def test_electronic_payment_requires_reference_for_manual_verification():
    user = User.objects.create_user(username="manual-cashier", email="manual@example.com")
    org = Organization.objects.create(name="Org", slug="manual-payment-org", status="active")
    company = Company.objects.create(organization=org, name="Company", code="CO")
    branch = Branch.objects.create(organization=org, company=company, name="Branch", code="BR")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="POS", location_type="pos")
    customer = Contact.objects.create(organization=org, contact_type="customer", name="Customer")
    session = POSSession.objects.create(organization=org, number="S1", location=location, cashier=user)
    sale = Sale.objects.create(
        organization=org, number="SALE1", session=session, location=location,
        customer=customer, total=1000, created_by=user,
    )
    payment = Payment.objects.create(
        organization=org, number="PAY1", customer=customer, sale=sale,
        method="mpesa", amount=1000, received_by=user,
    )

    with pytest.raises(ValidationError):
        confirm_payment(payment=payment, confirmed_by=user)


@pytest.mark.django_db
def test_confirm_refund_reconciles_receivable_and_payment_status():
    user = User.objects.create_user(username="refund-collector", email="refund@example.com")
    org = Organization.objects.create(name="Org", slug="refund-receivable-org", status="active")
    company = Company.objects.create(organization=org, name="Company", code="CO")
    branch = Branch.objects.create(organization=org, company=company, name="Branch", code="BR")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="POS", location_type="pos")
    customer = Contact.objects.create(organization=org, contact_type="customer", name="Customer")
    session = POSSession.objects.create(organization=org, number="S1", location=location, cashier=user)
    sale = Sale.objects.create(
        organization=org, number="SALE1", session=session, location=location,
        customer=customer, total=1000, paid_total=600, created_by=user,
    )
    receivable = Receivable.objects.create(
        organization=org, customer=customer, sale=sale,
        original_amount=400, outstanding_amount=400, due_on="2026-07-01",
    )
    payment = Payment.objects.create(
        organization=org, number="PAY1", customer=customer, sale=sale,
        method="cash", status=PaymentStatus.CONFIRMED, amount=600, received_by=user,
    )
    first = Refund.objects.create(
        organization=org, number="RFD1", payment=payment, amount=200, reason="Partial",
    )
    second = Refund.objects.create(
        organization=org, number="RFD2", payment=payment, amount=400, reason="Remaining",
    )

    confirm_refund(refund=first)
    receivable.refresh_from_db()
    payment.refresh_from_db()
    assert receivable.outstanding_amount == 600
    assert payment.status == PaymentStatus.CONFIRMED

    confirm_refund(refund=second)
    receivable.refresh_from_db()
    payment.refresh_from_db()
    assert receivable.outstanding_amount == 1000
    assert payment.status == PaymentStatus.REFUNDED

# Create your tests here.
