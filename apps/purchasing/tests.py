import pytest
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Product
from apps.contacts.models import Contact
from apps.inventory.models import StockBalance
from apps.organizations.models import Branch, Company, Location, Membership, MembershipStatus, Organization, Role
from apps.purchasing.models import PurchaseDiscrepancy, PurchaseOrder, SupplierReturn
from apps.operations.models import Payable


@pytest.mark.django_db
def test_purchase_staff_workflow_receives_stock(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="mobipos-electronics")
    supplier = Contact.objects.get(organization=organization, contact_type="supplier")
    destination = Location.objects.get(organization=organization, location_type="warehouse")
    product = Product.objects.get(organization=organization, sku="CHG-20W")
    client.force_login(user)

    response = client.post(reverse("purchase-create"), {
        "supplier": supplier.id, "destination": destination.id, "product": product.id,
        "quantity": "5", "unit_cost": "800", "notes": "Restock",
    })
    order = PurchaseOrder.objects.filter(organization=organization).exclude(number="").latest("created_at")
    assert response.status_code == 302
    assert client.post(reverse("purchase-approve", args=[order.id])).status_code == 302
    line = order.lines.get()
    assert client.post(reverse("purchase-receive", args=[line.id]), {"quantity": "5", "serial_numbers": ""}).status_code == 302

    order.refresh_from_db()
    assert order.status == "received"
    assert StockBalance.objects.get(organization=organization, product=product, location=destination).quantity == 5
    assert Payable.objects.get(purchase_order=order).outstanding_amount == 4000


@pytest.mark.django_db
def test_purchase_create_supports_multiple_lines(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="mobipos-electronics")
    supplier = Contact.objects.get(organization=organization, contact_type="supplier")
    destination = Location.objects.get(organization=organization, location_type="warehouse")
    products = list(Product.objects.filter(organization=organization).order_by("sku")[:2])
    client.force_login(user)

    response = client.post(reverse("purchase-create"), {
        "supplier": supplier.id, "destination": destination.id,
        "product": products[0].id, "quantity": "2", "unit_cost": "100",
        "product_2": products[1].id, "quantity_2": "3", "unit_cost_2": "200",
        "notes": "Multi-line restock",
    })

    order = PurchaseOrder.objects.filter(organization=organization).latest("created_at")
    assert response.status_code == 302
    assert order.lines.count() == 2
    assert set(order.lines.values_list("product_id", flat=True)) == {product.id for product in products}


@pytest.mark.django_db
def test_purchase_create_stores_supplier_reference_and_attachment(client, tmp_path):
    call_command("seed_demo_data")
    user = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="mobipos-electronics")
    supplier = Contact.objects.get(organization=organization, contact_type="supplier")
    destination = Location.objects.get(organization=organization, location_type="warehouse")
    product = Product.objects.get(organization=organization, sku="CHG-20W")
    client.force_login(user)

    upload = SimpleUploadedFile(
        "supplier-invoice.pdf",
        b"%PDF-1.4 demo supplier invoice",
        content_type="application/pdf",
    )
    with override_settings(MEDIA_ROOT=tmp_path):
        response = client.post(reverse("purchase-create"), {
            "supplier": supplier.id,
            "destination": destination.id,
            "supplier_reference": "SUP-INV-9001",
            "attachment": upload,
            "product": product.id,
            "quantity": "2",
            "unit_cost": "800",
            "notes": "Invoice attached",
        })
        order = PurchaseOrder.objects.filter(organization=organization).latest("created_at")
        detail_response = client.get(reverse("purchase-detail", args=[order.id]))

    assert response.status_code == 302
    assert order.supplier_reference == "SUP-INV-9001"
    assert order.attachment.name.endswith("supplier-invoice.pdf")
    assert detail_response.status_code == 200
    assert b"SUP-INV-9001" in detail_response.content
    assert b"Open attachment" in detail_response.content


@pytest.mark.django_db
def test_purchase_create_rejects_unsupported_supplier_attachment(client, tmp_path):
    call_command("seed_demo_data")
    user = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="mobipos-electronics")
    supplier = Contact.objects.get(organization=organization, contact_type="supplier")
    destination = Location.objects.get(organization=organization, location_type="warehouse")
    product = Product.objects.get(organization=organization, sku="CHG-20W")
    client.force_login(user)

    upload = SimpleUploadedFile(
        "supplier-invoice.exe",
        b"not a document",
        content_type="application/octet-stream",
    )
    with override_settings(MEDIA_ROOT=tmp_path):
        response = client.post(reverse("purchase-create"), {
            "supplier": supplier.id,
            "destination": destination.id,
            "supplier_reference": "BAD-FILE-9001",
            "attachment": upload,
            "product": product.id,
            "quantity": "2",
            "unit_cost": "800",
        })

    assert response.status_code == 200
    assert b"Unsupported supplier document type" in response.content
    assert not PurchaseOrder.objects.filter(organization=organization, supplier_reference="BAD-FILE-9001").exists()


@pytest.mark.django_db
def test_purchase_create_rejects_oversized_supplier_attachment(client, tmp_path):
    call_command("seed_demo_data")
    user = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="mobipos-electronics")
    supplier = Contact.objects.get(organization=organization, contact_type="supplier")
    destination = Location.objects.get(organization=organization, location_type="warehouse")
    product = Product.objects.get(organization=organization, sku="CHG-20W")
    client.force_login(user)

    upload = SimpleUploadedFile(
        "supplier-invoice.pdf",
        b"x" * (5 * 1024 * 1024 + 1),
        content_type="application/pdf",
    )
    with override_settings(MEDIA_ROOT=tmp_path):
        response = client.post(reverse("purchase-create"), {
            "supplier": supplier.id,
            "destination": destination.id,
            "supplier_reference": "BIG-FILE-9001",
            "attachment": upload,
            "product": product.id,
            "quantity": "2",
            "unit_cost": "800",
        })

    assert response.status_code == 200
    assert b"Supplier document must be 5 MB or smaller" in response.content
    assert not PurchaseOrder.objects.filter(organization=organization, supplier_reference="BIG-FILE-9001").exists()


@pytest.mark.django_db
def test_purchase_document_extraction_reads_csv_attachment(client, tmp_path):
    call_command("seed_demo_data")
    user = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="mobipos-electronics")
    supplier = Contact.objects.get(organization=organization, contact_type="supplier")
    destination = Location.objects.get(organization=organization, location_type="warehouse")
    product = Product.objects.get(organization=organization, sku="CHG-20W")
    client.force_login(user)

    upload = SimpleUploadedFile(
        "supplier-invoice.csv",
        b"sku,qty,unit_cost\nCHG-20W,4,800\n",
        content_type="text/csv",
    )
    with override_settings(MEDIA_ROOT=tmp_path):
        client.post(reverse("purchase-create"), {
            "supplier": supplier.id,
            "destination": destination.id,
            "supplier_reference": "CSV-9001",
            "attachment": upload,
            "product": product.id,
            "quantity": "4",
            "unit_cost": "800",
        })
        order = PurchaseOrder.objects.filter(organization=organization).latest("created_at")
        response = client.post(reverse("purchase-extract-document", args=[order.id]))
        order.refresh_from_db()

    assert response.status_code == 302
    assert order.extraction_status == "extracted"
    assert "CHG-20W | 4 | 800" in order.extracted_text


@pytest.mark.django_db
def test_purchase_document_extraction_handles_corrupted_spreadsheet(client, tmp_path):
    call_command("seed_demo_data")
    user = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="mobipos-electronics")
    supplier = Contact.objects.get(organization=organization, contact_type="supplier")
    destination = Location.objects.get(organization=organization, location_type="warehouse")
    product = Product.objects.get(organization=organization, sku="CHG-20W")
    client.force_login(user)

    upload = SimpleUploadedFile(
        "supplier-invoice.xlsx",
        b"not a real workbook",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    with override_settings(MEDIA_ROOT=tmp_path):
        client.post(reverse("purchase-create"), {
            "supplier": supplier.id,
            "destination": destination.id,
            "supplier_reference": "BAD-XLSX-9001",
            "attachment": upload,
            "product": product.id,
            "quantity": "4",
            "unit_cost": "800",
        })
        order = PurchaseOrder.objects.filter(organization=organization).latest("created_at")
        response = client.post(reverse("purchase-extract-document", args=[order.id]), follow=True)
        order.refresh_from_db()

    assert response.status_code == 200
    assert order.extraction_status == "failed"
    assert "Could not read the spreadsheet attachment" in order.extracted_text
    assert b"Could not read the spreadsheet attachment" in response.content


@pytest.mark.django_db
def test_purchase_create_rejects_cross_tenant_supplier(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="alice")
    own_org = Organization.objects.get(slug="mobipos-electronics")
    other_org = Organization.objects.get(slug="nairobi-mobile-hub")
    supplier = Contact.objects.get(organization=other_org, contact_type="supplier")
    destination = Location.objects.get(organization=own_org, location_type="warehouse")
    product = Product.objects.get(organization=own_org, sku="CHG-20W")
    client.force_login(user)

    response = client.post(reverse("purchase-create"), {
        "supplier": supplier.id, "destination": destination.id, "product": product.id,
        "quantity": "5", "unit_cost": "800",
    })

    assert response.status_code == 200
    assert not PurchaseOrder.objects.filter(organization=own_org, supplier=supplier).exists()


@pytest.mark.django_db
def test_non_owner_cannot_approve_purchase(client):
    call_command("seed_demo_data")
    organization = Organization.objects.get(slug="mobipos-electronics")
    staff = User.objects.create_user(username="purchase-staff", email="purchase-staff@example.com")
    Membership.objects.create(organization=organization, user=staff, status=MembershipStatus.ACTIVE)
    order = PurchaseOrder.objects.create(
        organization=organization, number="PO-AUTH",
        supplier=Contact.objects.get(organization=organization, contact_type="supplier"),
        destination=Location.objects.get(organization=organization, location_type="warehouse"),
        ordered_on="2026-06-12", created_by=staff,
    )
    client.force_login(staff)

    response = client.post(reverse("purchase-approve", args=[order.id]))

    assert response.status_code == 403


@pytest.mark.django_db
def test_purchase_form_rejects_unassigned_branch_location(client):
    call_command("seed_demo_data")
    organization = Organization.objects.get(slug="mobipos-electronics")
    staff = User.objects.create_user(username="limited-purchaser", email="limited-purchaser@example.com")
    membership = Membership.objects.create(organization=organization, user=staff, status=MembershipStatus.ACTIVE)
    role = Role.objects.create(organization=organization, name="Purchaser", code="purchaser")
    role.permissions.add(Permission.objects.get(content_type__app_label="purchasing", codename="add_purchaseorder"))
    membership.roles.add(role)
    assigned_branch = Branch.objects.get(organization=organization)
    membership.branches.add(assigned_branch)
    company = Company.objects.get(organization=organization)
    hidden_branch = Branch.objects.create(organization=organization, company=company, name="Hidden", code="HIDDEN")
    hidden_location = Location.objects.create(
        organization=organization, branch=hidden_branch, name="Hidden Warehouse",
        code="HIDDEN-WH", location_type="warehouse",
    )
    supplier = Contact.objects.get(organization=organization, contact_type="supplier")
    product = Product.objects.get(organization=organization, sku="CHG-20W")
    client.force_login(staff)

    response = client.post(reverse("purchase-create"), {
        "supplier": supplier.id, "destination": hidden_location.id, "product": product.id,
        "quantity": "1", "unit_cost": "800",
    })

    assert response.status_code == 200
    assert not PurchaseOrder.objects.filter(organization=organization, created_by=staff).exists()


@pytest.mark.django_db
def test_supplier_return_reduces_stock_and_payable(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="mobipos-electronics")
    supplier = Contact.objects.get(organization=organization, contact_type="supplier")
    destination = Location.objects.get(organization=organization, location_type="warehouse")
    product = Product.objects.get(organization=organization, sku="CHG-20W")
    client.force_login(user)
    client.post(reverse("purchase-create"), {
        "supplier": supplier.id, "destination": destination.id, "product": product.id,
        "quantity": "2", "unit_cost": "800",
    })
    order = PurchaseOrder.objects.filter(organization=organization).latest("created_at")
    client.post(reverse("purchase-approve", args=[order.id]))
    line = order.lines.get()
    client.post(reverse("purchase-receive", args=[line.id]), {"quantity": "2"})
    client.post(reverse("supplier-return-create"), {
        "line": line.id, "quantity": "1", "reason": "Damaged on receipt",
    })
    supplier_return = SupplierReturn.objects.get(line=line)

    client.post(reverse("supplier-return-complete", args=[supplier_return.id]))

    supplier_return.refresh_from_db()
    order.payable.refresh_from_db()
    assert supplier_return.status == "completed"
    assert StockBalance.objects.get(organization=organization, product=product, location=destination).quantity == 1
    assert order.payable.outstanding_amount == 800


@pytest.mark.django_db
def test_purchase_receipt_discrepancy_records_only_accepted_stock(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="mobipos-electronics")
    destination = Location.objects.get(organization=organization, location_type="warehouse")
    product = Product.objects.get(organization=organization, sku="CHG-20W")
    client.force_login(user)
    client.post(reverse("purchase-create"), {
        "supplier": Contact.objects.get(organization=organization, contact_type="supplier").id,
        "destination": destination.id, "product": product.id, "quantity": "5", "unit_cost": "800",
    })
    order = PurchaseOrder.objects.filter(organization=organization).latest("created_at")
    client.post(reverse("purchase-approve", args=[order.id]))
    line = order.lines.get()
    client.post(reverse("purchase-receive", args=[line.id]), {
        "quantity": "3", "damaged_quantity": "1", "close_with_discrepancy": "on",
        "discrepancy_reason": "One damaged and one missing",
    })

    order.refresh_from_db()
    issue = PurchaseDiscrepancy.objects.get(line=line)
    assert order.status == "discrepancy"
    assert issue.damaged_quantity == 1
    assert issue.missing_quantity == 1
    assert StockBalance.objects.get(organization=organization, product=product, location=destination).quantity == 3

    client.post(reverse("purchase-discrepancy-resolve", args=[issue.id]), {"resolution": "accept_short"})
    order.refresh_from_db()
    issue.refresh_from_db()
    assert order.status == "closed"
    assert issue.status == "resolved"
