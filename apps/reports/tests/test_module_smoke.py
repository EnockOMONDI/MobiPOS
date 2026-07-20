import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User


@pytest.mark.django_db
def test_all_staff_modules_search_and_export_render(client):
    call_command("seed_demo_data")
    client.force_login(User.objects.get(username="brian"))
    modules = (
        "products", "inventory", "stock", "purchases", "transfers", "sales",
        "payments", "expenses", "commissions", "repairs", "integrations",
        "receivables", "approvals", "returns", "refunds", "subscriptions",
    )
    for module in modules:
        assert client.get(reverse("module-overview", args=[module])).status_code == 200
    export = client.get(reverse("module-overview", args=["products"]), {"format": "csv"})
    assert export.status_code == 200
    assert export["Content-Type"] == "text/csv"
    search = client.get(reverse("global-search"), {"q": "A07"})
    assert search.status_code == 200
    assert b"A07 64GB/4GB" in search.content
    imei_workspace = client.get(reverse("imei-history"), {"q": "A07"})
    assert imei_workspace.status_code == 200
    assert b"IMEI and serial workspace" in imei_workspace.content
    assert b"A07 64GB/4GB" in imei_workspace.content
    available_imeis = client.get(reverse("imei-history"), {"status": "available"})
    assert available_imeis.status_code == 200
    assert available_imeis.context["page_obj"].paginator.count > 0
    assert client.get(reverse("operational-report")).status_code == 200
    assert client.get(reverse("retail-analytics-report")).status_code == 200
    retail_export = client.get(reverse("retail-analytics-report"), {"format": "csv"})
    assert retail_export.status_code == 200
    assert retail_export["Content-Type"] == "text/csv"
    assert client.get(reverse("agent-network-report")).status_code == 200
    agent_export = client.get(reverse("agent-network-report"), {"format": "csv"})
    assert agent_export.status_code == 200
    assert agent_export["Content-Type"] == "text/csv"
    assert client.get(reverse("exception-report")).status_code == 200


@pytest.mark.django_db
def test_module_overview_pdf_export_returns_pdf(client):
    call_command("seed_demo_data")
    client.force_login(User.objects.get(username="brian"))

    response = client.get(reverse("module-overview", args=["sales"]), {"format": "pdf"})

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


@pytest.mark.django_db
def test_product_register_column_controls_and_pdf_export(client):
    call_command("seed_demo_data")
    client.force_login(User.objects.get(username="brian"))

    page_response = client.get(reverse("module-overview", args=["products"]))
    pdf_response = client.get(reverse("module-overview", args=["products"]), {"format": "pdf"})

    assert page_response.status_code == 200
    assert b"Column visibility" in page_response.content
    assert b"data-column-table=\"product-register\"" in page_response.content
    assert pdf_response.status_code == 200
    assert pdf_response["Content-Type"] == "application/pdf"
    assert pdf_response.content.startswith(b"%PDF")
