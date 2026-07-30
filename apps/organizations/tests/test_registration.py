import pytest
from django.urls import reverse

from apps.catalog.models import Brand, Category
from apps.contacts.models import Contact
from apps.organizations.models import Branch, Location, Membership, Organization, Plan, Role, Subscription, SubscriptionInvoice


@pytest.mark.django_db
def test_self_service_registration_creates_active_owner_tenant(client):
    plan = Plan.objects.create(name="Starter", code="starter", monthly_price=1000)
    response = client.post(
        reverse("register-organization"),
        {
            "organization_name": "New Retailer",
            "organization_slug": "new-retailer",
            "first_name": "New",
            "last_name": "Owner",
            "username": "newowner",
            "email": "newowner@example.com",
            "password": "SecurePass123!",
            "plan": plan.id,
        },
    )

    organization = Organization.objects.get(slug="new-retailer")
    assert response.status_code == 302
    assert organization.status == "active"
    assert Membership.objects.get(organization=organization).is_owner
    subscription = Subscription.objects.get(organization=organization)
    assert subscription.status == "active"
    assert subscription.plan == plan
    assert subscription.starts_on
    assert subscription.renews_on
    assert Branch.objects.filter(organization=organization).exists()
    assert Location.objects.filter(organization=organization, location_type="pos").exists()
    assert SubscriptionInvoice.objects.get(organization=organization).amount == 1000
    assert Role.objects.filter(organization=organization, code="cashier").exists()
    assert Role.objects.filter(organization=organization, code="inventory-officer").exists()
    assert Category.objects.filter(organization=organization, code="phones").exists()
    assert Brand.objects.filter(organization=organization, name="Samsung").exists()
    assert Contact.objects.filter(organization=organization, name="Opening Stock Supplier", contact_type="supplier").exists()


@pytest.mark.django_db
def test_registration_form_creates_free_pilot_plan_when_no_plans_exist(client):
    response = client.get(reverse("register-organization"))

    assert response.status_code == 200
    pilot = Plan.objects.get(code="pilot")
    assert pilot.name == "Pilot"
    assert pilot.monthly_price == 0
    assert pilot.is_active
