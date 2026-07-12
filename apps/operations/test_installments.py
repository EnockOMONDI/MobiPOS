import pytest

from apps.accounts.models import User
from apps.contacts.models import Contact
from apps.operations.models import Receivable
from apps.operations.services import replace_installment_schedule
from apps.organizations.models import Branch, Company, Location, Organization
from apps.pos.models import POSSession
from apps.sales.models import Sale


@pytest.mark.django_db
def test_installment_schedule_must_allocate_receivable_balance():
    user = User.objects.create_user(username="schedule-owner", email="schedule@example.com")
    organization = Organization.objects.create(name="Schedule", slug="schedule", status="active")
    company = Company.objects.create(organization=organization, name="Schedule Ltd", code="SCH")
    branch = Branch.objects.create(organization=organization, company=company, name="Main", code="MAIN")
    location = Location.objects.create(organization=organization, branch=branch, name="POS", code="POS", location_type="pos")
    customer = Contact.objects.create(organization=organization, contact_type="customer", name="Customer")
    session = POSSession.objects.create(organization=organization, number="S1", location=location, cashier=user)
    sale = Sale.objects.create(
        organization=organization, number="SALE1", session=session, location=location,
        customer=customer, total=1000, paid_total=200, created_by=user,
    )
    receivable = Receivable.objects.create(
        organization=organization, customer=customer, sale=sale,
        original_amount=800, outstanding_amount=800, due_on="2026-07-30",
    )

    replace_installment_schedule(
        receivable=receivable,
        entries=[("2026-07-15", 300), ("2026-07-30", 500)],
    )

    assert list(receivable.installments.filter(is_current=True).values_list("sequence", "amount")) == [(1, 300), (2, 500)]


@pytest.mark.django_db
def test_installment_schedule_replacement_preserves_previous_versions():
    user = User.objects.create_user(username="version-owner", email="version@example.com")
    organization = Organization.objects.create(name="Version", slug="version-schedule", status="active")
    company = Company.objects.create(organization=organization, name="Version Ltd", code="VER")
    branch = Branch.objects.create(organization=organization, company=company, name="Main", code="MAIN")
    location = Location.objects.create(organization=organization, branch=branch, name="POS", code="POS", location_type="pos")
    customer = Contact.objects.create(organization=organization, contact_type="customer", name="Customer")
    session = POSSession.objects.create(organization=organization, number="S1", location=location, cashier=user)
    sale = Sale.objects.create(
        organization=organization, number="SALE1", session=session, location=location,
        customer=customer, total=1000, paid_total=200, created_by=user,
    )
    receivable = Receivable.objects.create(
        organization=organization, customer=customer, sale=sale,
        original_amount=800, outstanding_amount=800, due_on="2026-07-30",
    )

    replace_installment_schedule(receivable=receivable, entries=[("2026-07-15", 800)])
    replace_installment_schedule(receivable=receivable, entries=[("2026-07-20", 300), ("2026-07-30", 500)])

    assert receivable.installments.count() == 3
    assert list(receivable.installments.filter(is_current=False).values_list("schedule_version", "amount")) == [(1, 800)]
    assert list(receivable.installments.filter(is_current=True).values_list("schedule_version", "sequence", "amount")) == [
        (2, 1, 300),
        (2, 2, 500),
    ]
