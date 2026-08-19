import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.purchasing.models import PurchaseOrder
from apps.payments.models import Payment
from apps.operations.models import Payable
from apps.sales.models import Sale
from apps.reports.workspaces import REGISTER_CONFIG, operational_register_export
from apps.reports.exporting import safe_csv_cell
from django.test import RequestFactory


@pytest.fixture
def demo_owner(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    client.force_login(owner)
    return client


@pytest.mark.django_db
def test_sales_workspace_is_paginated_and_keeps_business_filters(demo_owner):
    response = demo_owner.get(reverse("sales-workspace"), {"date_from": "2099-01-01", "date_to": "2099-12-31"})
    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 0
    assert b"Sales register" in response.content


@pytest.mark.django_db
def test_payments_workspace_searches_and_paginates(demo_owner):
    payment = Payment.objects.filter(organization__slug="nairobi-mobile-hub").first()
    response = demo_owner.get(reverse("payments-workspace"), {"q": payment.number})
    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 1
    assert payment.number.encode() in response.content


@pytest.mark.django_db
def test_purchasing_workspace_is_tenant_scoped_and_has_detail_links(demo_owner):
    response = demo_owner.get(reverse("purchasing-workspace"))
    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == PurchaseOrder.objects.filter(
        organization__slug="nairobi-mobile-hub"
    ).count()
    assert b"New purchase" in response.content


@pytest.mark.django_db
def test_finance_workspaces_render_paginated_decisions(demo_owner):
    expenses = demo_owner.get(reverse("expenses-workspace"))
    payables = demo_owner.get(reverse("payables-workspace"))
    assert expenses.status_code == 200
    assert payables.status_code == 200
    assert payables.context["page_obj"].paginator.count == Payable.objects.filter(
        organization__slug="nairobi-mobile-hub"
    ).count()
    assert b"Supplier balances" in payables.content


@pytest.mark.django_db
def test_operational_registers_render_with_tenant_scope_and_pagination(demo_owner):
    for register, config in REGISTER_CONFIG.items():
        response = demo_owner.get(reverse("operational-register", args=[register]))
        assert response.status_code == 200, register
        assert response.context["title"] == config["title"]
        assert response.context["page_obj"].paginator.count >= 0


@pytest.mark.django_db
def test_operational_register_rejects_invalid_date_without_returning_rows(demo_owner):
    response = demo_owner.get(
        reverse("operational-register", args=["inventory"]),
        {"date_from": "not-a-date"},
    )
    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 0
    assert b"Enter a valid start date" in response.content


@pytest.mark.django_db
def test_operational_export_uses_workspace_columns_and_filters(demo_owner):
    request = RequestFactory().get(
        "/workspaces/inventory/",
        {"q": "a product that cannot exist", "date_from": "2099-01-01"},
    )
    request.user = User.objects.get(username="brian")
    request.organization = request.user.memberships.filter(status="active").first().organization
    response = operational_register_export(request, "inventory")
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/csv")
    assert response.content.splitlines()[0].decode() == ",".join(REGISTER_CONFIG["inventory"]["columns"])
    assert len(response.content.splitlines()) == 1


def test_csv_keeps_negative_numbers_numeric():
    assert safe_csv_cell(-120) == "-120"
    assert safe_csv_cell("-120") == "'-120"


@pytest.mark.django_db
def test_operational_pdf_export_uses_the_same_filtered_register(demo_owner):
    request = RequestFactory().get(
        "/workspaces/transfers/",
        {"format": "pdf", "q": "a transfer that cannot exist"},
    )
    request.user = User.objects.get(username="brian")
    request.organization = request.user.memberships.filter(status="active").first().organization

    response = operational_register_export(request, "transfers")

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"].endswith('.pdf"')
    assert response.content.startswith(b"%PDF")


@pytest.mark.django_db
def test_operational_workspace_preserves_selected_columns_in_page_and_export(demo_owner):
    params = {"columns": "0,2"}
    page = demo_owner.get(reverse("operational-register", args=["inventory"]), params)
    request = RequestFactory().get("/workspaces/inventory/", {**params, "format": "csv"})
    request.user = User.objects.get(username="brian")
    request.organization = request.user.memberships.filter(status="active").first().organization
    export = operational_register_export(request, "inventory")

    assert page.context["columns"] == ["IMEI / serial", "Location"]
    assert export.content.splitlines()[0].decode() == "IMEI / serial,Location"


@pytest.mark.django_db
def test_operational_workspace_preserves_repeated_column_checkboxes(demo_owner):
    params = [("columns", "0"), ("columns", "2")]
    page = demo_owner.get(reverse("operational-register", args=["inventory"]), params)
    assert page.status_code == 200
    assert page.context["columns"] == ["IMEI / serial", "Location"]
