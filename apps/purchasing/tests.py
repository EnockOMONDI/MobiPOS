import uuid
from decimal import Decimal

import pytest
from django.contrib.auth.models import Permission
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import User
from apps.catalog.models import Category, Product
from apps.contacts.models import Contact
from apps.inventory.models import StockBalance
from apps.organizations.models import Branch, Company, Location, Membership, MembershipStatus, Organization, Role
from apps.purchasing.forms import PurchaseOrderForm
from apps.purchasing.models import PurchaseDiscrepancy, PurchaseOrder, SupplierReturn
from apps.operations.models import Payable, PayablePayment
from apps.operations.payables import record_payable_payment, reverse_payable_payment


@pytest.mark.django_db
def test_purchase_staff_workflow_receives_stock(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    supplier = Contact.objects.filter(organization=organization, contact_type="supplier").order_by("name").first()
    destination = Location.objects.filter(organization=organization, location_type="warehouse").order_by("code").first()
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
def test_purchase_receipt_replay_does_not_double_post_stock(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    supplier = Contact.objects.filter(organization=organization, contact_type="supplier").first()
    destination = Location.objects.filter(organization=organization, location_type="warehouse").first()
    product = Product.objects.get(organization=organization, sku="CHG-20W")
    client.force_login(user)
    client.post(reverse("purchase-create"), {
        "supplier": supplier.id,
        "destination": destination.id,
        "product": product.id,
        "quantity": "5",
        "unit_cost": "800",
    })
    order = PurchaseOrder.objects.filter(organization=organization).latest("created_at")
    client.post(reverse("purchase-approve", args=[order.id]))
    line = order.lines.get()
    request_id = uuid.uuid4()
    payload = {"request_id": str(request_id), "quantity": "2", "serial_numbers": ""}

    first = client.post(reverse("purchase-receive", args=[line.id]), payload)
    second = client.post(reverse("purchase-receive", args=[line.id]), payload)

    line.refresh_from_db()
    assert first.status_code == 302
    assert second.status_code == 302
    assert line.received_quantity == 2
    assert StockBalance.objects.get(
        organization=organization, product=product, location=destination
    ).quantity == 2


@pytest.mark.django_db
def test_later_purchase_receipt_preserves_recorded_payable_progress(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    supplier = Contact.objects.filter(organization=organization, contact_type="supplier").first()
    destination = Location.objects.filter(organization=organization, location_type="warehouse").first()
    product = Product.objects.get(organization=organization, sku="CHG-20W")
    client.force_login(user)
    client.post(reverse("purchase-create"), {
        "supplier": supplier.id,
        "destination": destination.id,
        "product": product.id,
        "quantity": "5",
        "unit_cost": "800",
    })
    order = PurchaseOrder.objects.filter(organization=organization).latest("created_at")
    client.post(reverse("purchase-approve", args=[order.id]))
    line = order.lines.get()
    client.post(reverse("purchase-receive", args=[line.id]), {
        "request_id": str(uuid.uuid4()),
        "quantity": "2",
        "serial_numbers": "",
    })
    payable = Payable.objects.get(purchase_order=order)
    record_payable_payment(
        payable=payable,
        actor=user,
        request_id=uuid.uuid4(),
        amount=600,
        method="cash",
    )

    client.post(reverse("purchase-receive", args=[line.id]), {
        "request_id": str(uuid.uuid4()),
        "quantity": "3",
        "serial_numbers": "",
    })

    payable.refresh_from_db()
    assert payable.original_amount == 4000
    assert payable.outstanding_amount == 3400


@pytest.mark.django_db
def test_supplier_payment_is_partial_idempotent_and_reversible(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    payable = Payable.objects.filter(organization=organization, outstanding_amount__gte=1000).first()
    request_id = uuid.uuid4()

    payment, created = record_payable_payment(
        payable=payable,
        actor=owner,
        request_id=request_id,
        amount=Decimal("500.00"),
        method="mpesa",
        reference="SUPPLIER-MPESA-001",
    )
    replay, replay_created = record_payable_payment(
        payable=payable,
        actor=owner,
        request_id=request_id,
        amount=Decimal("500.00"),
        method="mpesa",
        reference="SUPPLIER-MPESA-001",
    )

    payable.refresh_from_db()
    assert created is True
    assert replay_created is False
    assert replay.id == payment.id
    assert PayablePayment.objects.filter(request_id=request_id).count() == 1
    assert payable.outstanding_amount == payable.original_amount - 500

    reverse_payable_payment(payment=payment, actor=owner, reason="Duplicate bank settlement")
    payable.refresh_from_db()
    payment.refresh_from_db()
    assert payment.reversed_at is not None
    assert payable.outstanding_amount == payable.original_amount


@pytest.mark.django_db
def test_supplier_payment_rejects_overpayment_and_duplicate_reference():
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    payables = list(Payable.objects.filter(organization=organization, outstanding_amount__gt=0)[:2])
    assert len(payables) == 2

    with pytest.raises(ValidationError, match="exceeds the outstanding balance"):
        record_payable_payment(
            payable=payables[0], actor=owner, request_id=uuid.uuid4(),
            amount=payables[0].outstanding_amount + 1, method="cash",
        )

    record_payable_payment(
        payable=payables[0], actor=owner, request_id=uuid.uuid4(),
        amount=1, method="bank", reference="BANK-REFERENCE-ONCE",
    )
    with pytest.raises(ValidationError, match="reference has already been used"):
        record_payable_payment(
            payable=payables[1], actor=owner, request_id=uuid.uuid4(),
            amount=1, method="bank", reference="BANK-REFERENCE-ONCE",
        )


@pytest.mark.django_db
def test_supplier_payment_record_is_immutable():
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    payable = Payable.objects.filter(organization__slug="nairobi-mobile-hub", outstanding_amount__gt=0).first()
    payment, _ = record_payable_payment(
        payable=payable, actor=owner, request_id=uuid.uuid4(), amount=1, method="cash",
    )
    payment.notes = "Changed"
    with pytest.raises(ValidationError, match="immutable"):
        payment.save()
    with pytest.raises(ValidationError, match="cannot be deleted"):
        payment.delete()


@pytest.mark.django_db
def test_payable_page_records_payment_and_is_tenant_scoped(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    payable = Payable.objects.filter(organization=organization, outstanding_amount__gt=100).first()
    other_org = Organization.objects.exclude(id=organization.id).first()
    other_payable = Payable.objects.filter(organization=other_org).first()
    client.force_login(owner)

    page = client.get(reverse("payable-detail", args=[payable.id]))
    posted = client.post(reverse("payable-payment-create", args=[payable.id]), {
        "request_id": str(uuid.uuid4()),
        "amount": "100.00",
        "method": "cash",
        "reference": "",
        "notes": "Part payment",
    })

    assert page.status_code == 200
    assert posted.status_code == 302
    assert PayablePayment.objects.filter(payable=payable, amount=100).exists()
    if other_payable:
        assert client.get(reverse("payable-detail", args=[other_payable.id])).status_code == 404


@pytest.mark.django_db
def test_purchase_create_supports_multiple_lines(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    supplier = Contact.objects.filter(organization=organization, contact_type="supplier").order_by("name").first()
    destination = Location.objects.filter(organization=organization, location_type="warehouse").order_by("code").first()
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
def test_purchase_form_auto_selects_only_available_destination():
    organization = Organization.objects.create(name="Setup Tenant", slug="setup-tenant", status="active")
    owner = User.objects.create_user(username="setup-owner", email="setup-owner@example.com")
    company = Company.objects.create(organization=organization, name="Setup Company", code="SETUP")
    branch = Branch.objects.create(organization=organization, company=company, name="Main Branch", code="MAIN")
    location = Location.objects.create(organization=organization, branch=branch, name="Main POS", code="MAIN-POS")
    membership = Membership.objects.create(
        organization=organization,
        user=owner,
        status=MembershipStatus.ACTIVE,
        is_owner=True,
    )
    membership.branches.add(branch)
    Contact.objects.create(organization=organization, contact_type="supplier", name="Setup Supplier")
    category = Category.objects.create(organization=organization, name="Phones", code="phones")
    Product.objects.create(organization=organization, category=category, name="Demo Phone", sku="DEMO-PHONE")

    form = PurchaseOrderForm(organization=organization, user=owner)

    assert list(form.fields["destination"].queryset) == [location]
    assert form.fields["destination"].initial == location


@pytest.mark.django_db
def test_purchase_create_page_links_setup_actions_back_to_purchase(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    client.force_login(user)

    response = client.get(reverse("purchase-create"))
    content = response.content.decode()

    assert response.status_code == 200
    assert f"{reverse('contact-create')}?type=supplier&next={reverse('purchase-create')}" in content
    assert f"{reverse('product-create')}?next={reverse('purchase-create')}" in content
    assert f"{reverse('location-create')}?next={reverse('purchase-create')}" in content


@pytest.mark.django_db
def test_purchase_create_guides_owner_when_only_opening_stock_supplier_exists(client):
    organization = Organization.objects.create(name="Supplier Setup", slug="supplier-setup", status="active")
    owner = User.objects.create_user(username="supplier-owner", email="supplier-owner@example.com")
    company = Company.objects.create(organization=organization, name="Supplier Company", code="SUP")
    branch = Branch.objects.create(organization=organization, company=company, name="Main Branch", code="MAIN")
    Location.objects.create(organization=organization, branch=branch, name="Main Warehouse", code="MAIN-WH", location_type="warehouse")
    membership = Membership.objects.create(
        organization=organization,
        user=owner,
        status=MembershipStatus.ACTIVE,
        is_owner=True,
    )
    membership.branches.add(branch)
    Contact.objects.create(organization=organization, contact_type="supplier", name="Opening Stock Supplier")
    category = Category.objects.create(organization=organization, name="Phones", code="phones")
    Product.objects.create(organization=organization, category=category, name="Demo Phone", sku="DEMO-PHONE")
    client.force_login(owner)

    response = client.get(reverse("purchase-create"))

    assert response.status_code == 200
    assert b"Add your real supplier before going live" in response.content


@pytest.mark.django_db
def test_purchase_create_stores_supplier_reference_and_attachment(client, tmp_path):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    supplier = Contact.objects.filter(organization=organization, contact_type="supplier").order_by("name").first()
    destination = Location.objects.filter(organization=organization, location_type="warehouse").order_by("code").first()
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
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    supplier = Contact.objects.filter(organization=organization, contact_type="supplier").order_by("name").first()
    destination = Location.objects.filter(organization=organization, location_type="warehouse").order_by("code").first()
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
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    supplier = Contact.objects.filter(organization=organization, contact_type="supplier").order_by("name").first()
    destination = Location.objects.filter(organization=organization, location_type="warehouse").order_by("code").first()
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
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    supplier = Contact.objects.filter(organization=organization, contact_type="supplier").order_by("name").first()
    destination = Location.objects.filter(organization=organization, location_type="warehouse").order_by("code").first()
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
def test_purchase_create_rejects_spreadsheet_with_invalid_contents(client, tmp_path):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    supplier = Contact.objects.filter(organization=organization, contact_type="supplier").order_by("name").first()
    destination = Location.objects.filter(organization=organization, location_type="warehouse").order_by("code").first()
    product = Product.objects.get(organization=organization, sku="CHG-20W")
    client.force_login(user)

    upload = SimpleUploadedFile(
        "supplier-invoice.xlsx",
        b"not a real workbook",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    with override_settings(MEDIA_ROOT=tmp_path):
        response = client.post(reverse("purchase-create"), {
            "supplier": supplier.id,
            "destination": destination.id,
            "supplier_reference": "BAD-XLSX-9001",
            "attachment": upload,
            "product": product.id,
            "quantity": "4",
            "unit_cost": "800",
        })

    assert response.status_code == 200
    assert b"contents do not match the .xlsx file type" in response.content
    assert not PurchaseOrder.objects.filter(
        organization=organization,
        supplier_reference="BAD-XLSX-9001",
    ).exists()


@pytest.mark.django_db
def test_purchase_document_extraction_handles_legacy_corrupted_spreadsheet(client, tmp_path):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    supplier = Contact.objects.filter(organization=organization, contact_type="supplier").order_by("name").first()
    destination = Location.objects.filter(organization=organization, location_type="warehouse").order_by("code").first()
    client.force_login(user)

    upload = SimpleUploadedFile(
        "legacy-corrupt.xlsx",
        b"not a real workbook",
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    with override_settings(MEDIA_ROOT=tmp_path):
        order = PurchaseOrder.objects.create(
            organization=organization,
            number="PO-LEGACY-CORRUPT",
            supplier=supplier,
            destination=destination,
            ordered_on=timezone.localdate(),
            supplier_reference="LEGACY-BAD-XLSX",
            attachment=upload,
            created_by=user,
        )
        response = client.post(reverse("purchase-extract-document", args=[order.id]), follow=True)
        order.refresh_from_db()

    assert response.status_code == 200
    assert order.extraction_status == "failed"
    assert "Could not read the spreadsheet attachment" in order.extracted_text
    assert b"Could not read the spreadsheet attachment" in response.content


@pytest.mark.django_db
def test_purchase_create_rejects_cross_tenant_supplier(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    own_org = Organization.objects.get(slug="nairobi-mobile-hub")
    other_org = Organization.objects.create(name="Other Purchase Tenant", slug="other-purchase-tenant", status="active")
    supplier = Contact.objects.create(
        organization=other_org,
        contact_type="supplier",
        name="Other Tenant Supplier",
        phone_number="+254744400001",
    )
    destination = Location.objects.filter(organization=own_org, location_type="warehouse").order_by("code").first()
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
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    staff = User.objects.create_user(username="purchase-staff", email="purchase-staff@example.com")
    Membership.objects.create(organization=organization, user=staff, status=MembershipStatus.ACTIVE)
    order = PurchaseOrder.objects.create(
        organization=organization, number="PO-AUTH",
        supplier=Contact.objects.filter(organization=organization, contact_type="supplier").order_by("name").first(),
        destination=Location.objects.filter(organization=organization, location_type="warehouse").order_by("code").first(),
        ordered_on="2026-06-12", created_by=staff,
    )
    client.force_login(staff)

    response = client.post(reverse("purchase-approve", args=[order.id]))

    assert response.status_code == 403


@pytest.mark.django_db
def test_purchase_form_rejects_unassigned_branch_location(client):
    call_command("seed_demo_data")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    staff = User.objects.create_user(username="limited-purchaser", email="limited-purchaser@example.com")
    membership = Membership.objects.create(organization=organization, user=staff, status=MembershipStatus.ACTIVE)
    role = Role.objects.create(organization=organization, name="Purchaser", code="purchaser")
    role.permissions.add(Permission.objects.get(content_type__app_label="purchasing", codename="add_purchaseorder"))
    membership.roles.add(role)
    assigned_branch = Branch.objects.get(organization=organization, code="WST")
    membership.branches.add(assigned_branch)
    company = Company.objects.filter(organization=organization).order_by("code").first()
    hidden_branch = Branch.objects.create(organization=organization, company=company, name="Hidden", code="HIDDEN")
    hidden_location = Location.objects.create(
        organization=organization, branch=hidden_branch, name="Hidden Warehouse",
        code="HIDDEN-WH", location_type="warehouse",
    )
    supplier = Contact.objects.filter(organization=organization, contact_type="supplier").order_by("name").first()
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
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    supplier = Contact.objects.filter(organization=organization, contact_type="supplier").order_by("name").first()
    destination = Location.objects.filter(organization=organization, location_type="warehouse").order_by("code").first()
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
    user = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    destination = Location.objects.filter(organization=organization, location_type="warehouse").order_by("code").first()
    product = Product.objects.get(organization=organization, sku="CHG-20W")
    client.force_login(user)
    client.post(reverse("purchase-create"), {
        "supplier": Contact.objects.filter(organization=organization, contact_type="supplier").order_by("name").first().id,
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
