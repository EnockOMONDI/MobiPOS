import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User


@pytest.mark.django_db
def test_all_staff_modules_search_and_export_render(client):
    call_command("seed_demo_data")
    client.force_login(User.objects.get(username="alice"))
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
