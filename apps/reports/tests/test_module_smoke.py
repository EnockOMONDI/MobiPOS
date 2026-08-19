import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.inventory.models import StockBalance
from apps.organizations.models import Location, Membership, MembershipStatus, Organization
from apps.reports.models import ReportExport, ReportExportStatus
from apps.reports.tasks import generate_report_export


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
def test_operational_and_exception_reports_paginate_stock_without_truncation(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    location = Location.objects.filter(organization=organization).first()
    category = Product.objects.filter(organization=organization).first().category
    products = Product.objects.bulk_create([
        Product(
            organization=organization,
            category=category,
            name=f"ZZZ report product {index:02d}",
            sku=f"REPORT-PAGE-{index:02d}",
            reorder_level=1,
        )
        for index in range(30)
    ])
    StockBalance.objects.bulk_create([
        StockBalance(organization=organization, product=product, location=location, quantity=0)
        for product in products
    ])
    client.force_login(user)

    operational = client.get(reverse("operational-report"), {"stock_page": 2})
    exceptions = client.get(reverse("exception-report"), {"low_stock_page": 2})

    assert operational.status_code == 200
    assert operational.context["stock_balances"].paginator.count >= 30
    assert operational.context["stock_balances"].number == 2
    assert exceptions.status_code == 200
    assert exceptions.context["low_stock"].paginator.count >= 30
    assert exceptions.context["low_stock"].number == 2


@pytest.mark.django_db
def test_module_overview_pdf_export_returns_pdf(client):
    call_command("seed_demo_data")
    client.force_login(User.objects.get(username="brian"))

    response = client.get(reverse("module-overview", args=["sales"]), {"format": "pdf"})

    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response.content.startswith(b"%PDF")


@pytest.mark.django_db
def test_module_overview_applies_business_date_filters_to_page_and_export(client):
    call_command("seed_demo_data")
    client.force_login(User.objects.get(username="brian"))

    page = client.get(
        reverse("module-overview", args=["sales"]),
        {"date_from": "2099-01-01", "date_to": "2099-12-31"},
    )
    export = client.get(
        reverse("module-overview", args=["sales"]),
        {"date_from": "2099-01-01", "date_to": "2099-12-31", "format": "csv"},
    )

    assert page.status_code == 200
    assert page.context["page_obj"].paginator.count == 0
    assert page.context["date_from"] == "2099-01-01"
    assert export.status_code == 200
    assert export.content.count(b"\n") == 1


@pytest.mark.django_db
def test_module_overview_rejects_reversed_date_range(client):
    call_command("seed_demo_data")
    client.force_login(User.objects.get(username="brian"))

    response = client.get(
        reverse("module-overview", args=["payments"]),
        {"date_from": "2026-08-31", "date_to": "2026-08-01"},
    )

    assert response.status_code == 200
    assert b"The start date must be on or before the end date." in response.content


@pytest.mark.django_db
def test_large_module_export_is_queued_generated_and_tenant_protected(client, settings, tmp_path):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    client.force_login(owner)
    settings.REPORT_ASYNC_EXPORT_THRESHOLD = 1
    settings.MEDIA_ROOT = tmp_path

    queued = client.get(
        reverse("module-overview", args=["sales"]),
        {"format": "csv", "date_from": "2020-01-01"},
    )

    export = ReportExport.objects.get(requested_by=owner, module="sales")
    assert queued.status_code == 302
    assert queued.url == reverse("report-export-detail", args=[export.id])
    assert export.status == ReportExportStatus.PENDING
    assert export.row_count > 0

    assert generate_report_export(str(export.id)) == ReportExportStatus.READY
    export.refresh_from_db()
    assert export.file
    download = client.get(reverse("report-export-download", args=[export.id]))
    assert download.status_code == 200
    assert download["Content-Disposition"].startswith("attachment;")

    outsider = User.objects.create_user(username="export-outsider", email="export-outsider@example.com")
    other_organization = Organization.objects.create(name="Other Export Org", slug="other-export-org", status="active")
    Membership.objects.create(
        user=outsider,
        organization=other_organization,
        status=MembershipStatus.ACTIVE,
        is_owner=True,
    )
    client.force_login(outsider)
    assert client.get(reverse("report-export-detail", args=[export.id])).status_code == 404


@pytest.mark.django_db
def test_background_export_preserves_repeated_workspace_filters(client, settings, tmp_path):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    client.force_login(owner)
    settings.REPORT_ASYNC_EXPORT_THRESHOLD = 1
    settings.MEDIA_ROOT = tmp_path

    response = client.get(
        reverse("operational-register", args=["inventory"]),
        [("columns", "0"), ("columns", "2"), ("delivery", "background"), ("format", "csv")],
    )

    assert response.status_code == 302
    export = ReportExport.objects.get(requested_by=owner, module="inventory")
    assert export.filters["columns"] == "0,2"


@pytest.mark.django_db
@pytest.mark.parametrize("module", ["products", "aged-stock"])
def test_priority_register_exports_can_run_in_background(client, settings, tmp_path, module):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    client.force_login(owner)
    settings.MEDIA_ROOT = tmp_path

    queued = client.get(
        reverse("module-overview", args=[module]),
        {"format": "csv", "delivery": "background"},
    )

    export = ReportExport.objects.get(requested_by=owner, module=module)
    assert queued.status_code == 302
    assert queued.url == reverse("report-export-detail", args=[export.id])
    assert generate_report_export(str(export.id)) == ReportExportStatus.READY
    export.refresh_from_db()
    assert export.file


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


@pytest.mark.django_db
def test_owner_register_pages_show_setup_create_actions(client):
    call_command("seed_demo_data")
    client.force_login(User.objects.get(username="brian"))

    branches = client.get(reverse("module-overview", args=["branches"]))
    locations = client.get(reverse("module-overview", args=["locations"]))
    contacts = client.get(reverse("module-overview", args=["contacts"]))

    assert branches.status_code == 200
    assert b"Add branch" in branches.content
    assert reverse("branch-create") in branches.content.decode()
    assert locations.status_code == 200
    assert b"Add location" in locations.content
    assert reverse("location-create") in locations.content.decode()
    assert contacts.status_code == 200
    assert b"Add customer or supplier" in contacts.content
    assert reverse("contact-create") in contacts.content.decode()


@pytest.mark.django_db
def test_setup_forms_show_business_friendly_examples(client):
    call_command("seed_demo_data")
    client.force_login(User.objects.get(username="brian"))

    branch_response = client.get(reverse("branch-create"))
    location_response = client.get(reverse("location-create"))
    supplier_response = client.get(reverse("contact-create"), {"type": "supplier"})

    assert branch_response.status_code == 200
    assert b"e.g. Mombasa Shop" in branch_response.content
    assert b"e.g. MSA" in branch_response.content
    assert b"e.g. mombasa@mobipos.com" in branch_response.content
    assert b"Examples: MSA, CBD, TRM." in branch_response.content
    assert location_response.status_code == 200
    assert b"e.g. Mombasa Warehouse" in location_response.content
    assert b"e.g. MSA-WH" in location_response.content
    assert b"location sits under a branch" in location_response.content
    assert supplier_response.status_code == 200
    assert b"Tax number / KRA PIN" in supplier_response.content
    assert b"e.g. P051234567A" in supplier_response.content
