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
    organization = Organization.objects.get(slug="mobipos-electronics")
    sale = Sale.objects.get(organization=organization, number="DEMO-SALE-001")
    original_balance = StockBalance.objects.get(organization=organization, product=sale.lines.get().product, location=sale.location).quantity
    client.force_login(user)
    sale_line = sale.lines.get()
    client.post(reverse("sale-request-return", args=[sale.id]), {
        "outcome": "refund",
        "disposition": "restock",
        "reason": "Customer changed mind",
        "refund_amount": "1500",
        f"quantity_{sale_line.id}": "1",
    })
    sale_return = SaleReturn.objects.get(sale=sale)

    response = client.post(reverse("return-complete", args=[sale_return.id]))

    sale.refresh_from_db()
    assert response.status_code == 302
    assert sale.status == "returned"
    assert StockBalance.objects.get(organization=organization, product=sale.lines.get().product, location=sale.location).quantity == original_balance + 1
    assert sale_return.sale.payments.get().refunds.exists()


@pytest.mark.django_db
def test_partial_line_return_allocates_refund_across_split_payments(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="mobipos-electronics")
    sale = Sale.objects.get(organization=organization, number="DEMO-SALE-001")
    line = sale.lines.get()
    line.quantity = 2
    line.line_total = 3000
    line.tax = 480
    line.save(update_fields=["quantity", "line_total", "tax", "updated_at"])
    sale.subtotal = 3000
    sale.tax_total = 480
    sale.total = 3480
    sale.paid_total = 3480
    sale.save(update_fields=["subtotal", "tax_total", "total", "paid_total", "updated_at"])
    payment = sale.payments.get()
    payment.amount = 1500
    payment.save(update_fields=["amount", "updated_at"])
    from apps.payments.models import Payment, PaymentStatus
    Payment.objects.create(
        organization=organization, number="DEMO-PAY-SPLIT", customer=sale.customer, sale=sale,
        method="cash", status=PaymentStatus.CONFIRMED, amount=1980, received_by=user,
    )
    client.force_login(user)

    client.post(reverse("sale-request-return", args=[sale.id]), {
        "outcome": "refund",
        "disposition": "restock",
        "reason": "Return one item",
        "refund_amount": "1740",
        f"quantity_{line.id}": "1",
    })
    sale_return = SaleReturn.objects.get(sale=sale)
    response = client.post(reverse("return-complete", args=[sale_return.id]))

    line.refresh_from_db()
    sale.refresh_from_db()
    assert response.status_code == 302
    assert line.returned_quantity == 1
    assert sale.status == "paid"
    assert sale.paid_total == 1740
    assert sale.payments.filter(refunds__status="refunded").distinct().count() == 2

# Create your tests here.
