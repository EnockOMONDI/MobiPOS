import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.contacts.models import Contact
from apps.organizations.models import Organization


@pytest.mark.django_db
def test_contact_duplicate_validation_and_lifecycle(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    contact = Contact.objects.filter(organization=organization).first()
    other_org = Organization.objects.create(name="Other Contact Tenant", slug="other-contact-tenant", status="active")
    other_contact = Contact.objects.create(
        organization=other_org,
        contact_type="customer",
        name="Other Tenant Customer",
        phone_number="+254733300001",
    )
    contact.email = "unique-contact@example.com"
    contact.save(update_fields=["email", "updated_at"])
    client.force_login(owner)

    duplicate = client.post(reverse("contact-create"), {
        "contact_type": "customer", "name": "Duplicate Customer",
        "email": "unique-contact@example.com", "credit_limit": 0,
        "payment_terms_days": 0, "is_active": "on",
    })
    detail = client.get(reverse("contact-detail", args=[contact.id]))
    forbidden = client.get(reverse("contact-detail", args=[other_contact.id]))
    update = client.post(reverse("contact-update", args=[contact.id]), {
        "contact_type": contact.contact_type,
        "name": f"{contact.name} Updated",
        "phone_number": contact.phone_number,
        "email": contact.email,
        "tax_number": contact.tax_number,
        "address": contact.address,
        "credit_limit": contact.credit_limit,
        "payment_terms_days": contact.payment_terms_days,
        "is_active": "on",
    })
    client.post(reverse("contact-toggle-active", args=[contact.id]))

    contact.refresh_from_db()
    assert duplicate.status_code == 200
    assert b"This email address is already used" in duplicate.content
    assert detail.status_code == 200
    assert forbidden.status_code == 404
    assert update.status_code == 302
    assert not contact.is_active
    assert AuditEvent.objects.filter(target_id=str(contact.id), action="contact.updated").exists()
    assert AuditEvent.objects.filter(target_id=str(contact.id), action="contact.archived").exists()


@pytest.mark.django_db
def test_contact_create_can_open_as_supplier_flow(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    client.force_login(owner)

    response = client.get(reverse("contact-create"), {"type": "supplier"})

    assert response.status_code == 200
    assert b"Add supplier" in response.content
    assert b'<option value="supplier" selected>' in response.content


@pytest.mark.django_db
def test_contact_create_returns_to_safe_purchase_setup_flow(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    client.force_login(owner)

    response = client.post(reverse("contact-create"), {
        "contact_type": "supplier",
        "name": "Return Flow Supplier",
        "phone_number": "+254700111222",
        "email": "return.supplier@example.com",
        "tax_number": "P000111222X",
        "credit_limit": 0,
        "payment_terms_days": 14,
        "is_active": "on",
        "next": reverse("purchase-create"),
    })

    assert response.status_code == 302
    assert response.url == reverse("purchase-create")
    assert Contact.objects.filter(organization=organization, name="Return Flow Supplier").exists()


@pytest.mark.django_db
def test_contact_statement_is_tenant_scoped_and_renders_finance_sections(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    contact = Contact.objects.filter(organization=organization, contact_type__in=("customer", "both")).first()
    other_org = Organization.objects.create(name="Other Statement Tenant", slug="other-statement-tenant", status="active")
    other_contact = Contact.objects.create(
        organization=other_org,
        contact_type="customer",
        name="Other Statement Customer",
        phone_number="+254733300002",
    )
    client.force_login(owner)

    response = client.get(reverse("contact-statement", args=[contact.id]))
    forbidden = client.get(reverse("contact-statement", args=[other_contact.id]))

    assert response.status_code == 200
    assert b"Statement" in response.content
    assert b"Receivables" in response.content
    assert b"Payables" in response.content
    assert forbidden.status_code == 404
