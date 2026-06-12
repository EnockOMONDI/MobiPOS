import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.inventory.models import StockBalance
from apps.organizations.models import Organization
from apps.sales.models import Sale, SaleReturn


@pytest.mark.django_db
def test_return_workflow_restocks_and_creates_refund(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="kipekee-electronics")
    sale = Sale.objects.get(organization=organization, number="DEMO-SALE-001")
    original_balance = StockBalance.objects.get(organization=organization, product=sale.lines.get().product, location=sale.location).quantity
    client.force_login(user)
    client.post(reverse("sale-request-return", args=[sale.id]), {"reason": "Customer changed mind", "refund_amount": "1500"})
    sale_return = SaleReturn.objects.get(sale=sale)

    response = client.post(reverse("return-complete", args=[sale_return.id]))

    sale.refresh_from_db()
    assert response.status_code == 302
    assert sale.status == "returned"
    assert StockBalance.objects.get(organization=organization, product=sale.lines.get().product, location=sale.location).quantity == original_balance + 1
    assert sale_return.sale.payments.get().refunds.exists()

# Create your tests here.
