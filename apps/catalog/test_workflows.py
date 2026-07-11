import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import Category, Product
from apps.audit.models import AuditEvent
from apps.contacts.models import Contact
from apps.organizations.models import Organization


@pytest.mark.django_db
def test_owner_can_create_catalog_and_contact_records(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="mobipos-electronics")
    client.force_login(owner)

    client.post(reverse("category-create"), {"name": "New Category", "code": "new-category"})
    category = Category.objects.get(organization=organization, code="new-category")
    response = client.post(reverse("product-create"), {
        "category": category.id, "name": "New Item", "sku": "NEW-ITEM",
        "is_stocked": "on", "is_sellable": "on", "is_purchasable": "on", "is_active": "on",
        "reorder_level": "2", "cost_price": "100", "selling_price": "150", "tax_rate": "16",
        "warranty_days": "0",
    })
    client.post(reverse("contact-create"), {
        "contact_type": "customer", "name": "New Customer", "credit_limit": "5000",
        "payment_terms_days": "30", "is_active": "on",
    })

    assert response.status_code == 302
    assert Product.objects.filter(organization=organization, sku="NEW-ITEM").exists()
    assert Contact.objects.filter(organization=organization, name="New Customer").exists()


@pytest.mark.django_db
def test_duplicate_catalog_identifiers_are_form_errors(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="mobipos-electronics")
    client.force_login(owner)

    category_response = client.post(reverse("category-create"), {"name": "Duplicate", "code": "phones"})
    product_response = client.post(reverse("product-create"), {
        "category": Category.objects.get(organization=organization, code="phones").id,
        "name": "Duplicate Phone",
        "sku": "MP-A07-64",
        "is_stocked": "on",
        "is_sellable": "on",
        "is_purchasable": "on",
        "is_active": "on",
    })

    assert category_response.status_code == 200
    assert b"This category code is already in use." in category_response.content
    assert product_response.status_code == 200
    assert b"This product SKU is already in use." in product_response.content


@pytest.mark.django_db
def test_product_lifecycle_is_tenant_scoped_and_audited(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="mobipos-electronics")
    product = Product.objects.filter(organization=organization).first()
    other_product = Product.objects.exclude(organization=organization).first()
    client.force_login(owner)

    detail = client.get(reverse("product-detail", args=[product.id]))
    forbidden = client.get(reverse("product-detail", args=[other_product.id]))
    update = client.post(reverse("product-update", args=[product.id]), {
        "category": product.category_id,
        "brand": product.brand_id or "",
        "name": f"{product.name} Updated",
        "sku": product.sku,
        "barcode": product.barcode,
        "description": product.description,
        "is_serialized": "on" if product.is_serialized else "",
        "is_stocked": "on" if product.is_stocked else "",
        "is_sellable": "on" if product.is_sellable else "",
        "is_purchasable": "on" if product.is_purchasable else "",
        "warranty_days": product.warranty_days,
        "reorder_level": product.reorder_level,
        "cost_price": product.cost_price,
        "selling_price": product.selling_price,
        "tax_rate": product.tax_rate,
        "is_active": "on",
    })
    client.post(reverse("product-toggle-active", args=[product.id]))

    product.refresh_from_db()
    assert detail.status_code == 200
    assert forbidden.status_code == 404
    assert update.status_code == 302
    assert not product.is_active
    assert AuditEvent.objects.filter(target_id=str(product.id), action="product.updated").exists()
    assert AuditEvent.objects.filter(target_id=str(product.id), action="product.archived").exists()
