from decimal import Decimal

import pytest
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Category, Product
from apps.commissions.models import CommissionAccrual, CommissionRule
from apps.commissions.services import refresh_sale_commissions
from apps.organizations.models import Branch, Company, Location, Organization
from apps.pos.models import POSSession
from apps.sales.models import Sale, SaleLine


@pytest.mark.django_db
def test_commission_becomes_payable_after_full_collection():
    agent = User.objects.create_user(username="commission-agent", email="commission@example.com")
    org = Organization.objects.create(name="Commission Org", slug="commission-org", status="active")
    company = Company.objects.create(organization=org, name="Company", code="CO")
    branch = Branch.objects.create(organization=org, company=company, name="Branch", code="BR")
    location = Location.objects.create(organization=org, branch=branch, name="POS", code="POS", location_type="pos")
    category = Category.objects.create(organization=org, name="Products", code="products")
    product = Product.objects.create(organization=org, category=category, name="Phone", sku="PHONE")
    session = POSSession.objects.create(organization=org, number="SESSION", location=location, cashier=agent)
    sale = Sale.objects.create(
        organization=org, number="SALE", session=session, location=location,
        agent=agent, created_by=agent, total=1000, paid_total=500,
    )
    SaleLine.objects.create(
        organization=org, sale=sale, product=product, quantity=1,
        unit_price=1000, line_total=1000,
    )
    CommissionRule.objects.create(
        organization=org, name="Five percent", percentage=5, requires_full_payment=True,
    )

    refresh_sale_commissions(sale=sale)
    accrual = CommissionAccrual.objects.get(sale=sale)
    assert accrual.amount == Decimal("50")
    assert not accrual.is_payable

    sale.paid_total = 1000
    sale.save(update_fields=["paid_total", "updated_at"])
    refresh_sale_commissions(sale=sale)
    accrual.refresh_from_db()
    assert accrual.is_payable


@pytest.mark.django_db
def test_owner_approves_and_pays_commission_batch(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="alice")
    org = Organization.objects.get(slug="kipekee-electronics")
    client.force_login(owner)
    today = timezone.localdate()

    response = client.post(reverse("commission-payout-create"), {
        "agent": owner.id, "period_start": today, "period_end": today,
    })

    from apps.commissions.models import CommissionPayout
    payout = CommissionPayout.objects.get(organization=org)
    assert response.status_code == 302
    assert payout.amount > 0
    client.post(reverse("commission-payout-approve", args=[payout.id]))
    client.post(reverse("commission-payout-pay", args=[payout.id]), {"payment_reference": "BANK-1"})
    payout.refresh_from_db()
    assert payout.status == "paid"
    assert payout.lines.filter(accrual__paid_at__isnull=False).count() == payout.lines.count()
