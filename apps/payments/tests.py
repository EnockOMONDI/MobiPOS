import pytest

from apps.accounts.models import User
from apps.catalog.models import Category, Product
from apps.contacts.models import Contact
from apps.organizations.models import Branch, Company, Location, Organization
from apps.operations.models import Receivable
from apps.payments.models import Payment, PaymentStatus
from apps.payments.services import confirm_payment
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
    payment = Payment.objects.create(
        organization=org, number="PAY1", customer=customer, sale=sale,
        method="cash", amount=400, received_by=user,
    )

    confirm_payment(payment=payment)

    receivable.refresh_from_db()
    assert receivable.outstanding_amount == 600

# Create your tests here.
