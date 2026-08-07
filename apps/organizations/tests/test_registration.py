import pytest
from django.urls import reverse

from apps.accounts.models import User
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
            "first_name": "New",
            "last_name": "Owner",
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
    branch = Branch.objects.get(organization=organization)
    assert branch.name == "Example Branch"
    assert branch.code == "EXAMPLE"
    location = Location.objects.get(organization=organization, location_type="pos")
    assert location.name == "Example POS"
    assert location.code == "EXAMPLE-POS"
    assert SubscriptionInvoice.objects.get(organization=organization).amount == 1000
    assert Role.objects.filter(organization=organization, code="cashier").exists()
    assert Role.objects.filter(organization=organization, code="inventory-officer").exists()
    assert Category.objects.filter(organization=organization, code="phones").exists()
    assert Brand.objects.filter(organization=organization, name="Samsung").exists()
    assert Contact.objects.filter(organization=organization, name="Opening Stock Supplier", contact_type="supplier").exists()
    assert User.objects.get(email="newowner@example.com").username == "newowner"


@pytest.mark.django_db
def test_self_service_registration_generates_unique_slug_and_username(client):
    plan = Plan.objects.create(name="Starter", code="starter", monthly_price=1000)
    Organization.objects.create(name="Existing Retailer", slug="new-retailer", status="active")
    User.objects.create_user(username="newowner", email="existing-owner@example.com")

    response = client.post(
        reverse("register-organization"),
        {
            "organization_name": "New Retailer",
            "first_name": "New",
            "last_name": "Owner",
            "email": "newowner@example.com",
            "password": "SecurePass123!",
            "plan": plan.id,
        },
    )

    assert response.status_code == 302
    assert Organization.objects.filter(slug="new-retailer-2").exists()
    assert User.objects.get(email="newowner@example.com").username == "newowner-2"


@pytest.mark.django_db
def test_registration_page_hides_internal_slug_and_username_fields(client):
    Plan.objects.create(name="Starter", code="starter", monthly_price=1000)

    response = client.get(reverse("register-organization"))

    assert response.status_code == 200
    content = response.content.decode()
    assert 'name="organization_slug"' not in content
    assert 'name="username"' not in content
    assert 'name="organization_name"' in content
    assert 'name="email"' in content


@pytest.mark.django_db
def test_registration_form_creates_free_pilot_plan_when_no_plans_exist(client):
    response = client.get(reverse("register-organization"))

    assert response.status_code == 200
    pilot = Plan.objects.get(code="pilot")
    assert pilot.name == "Pilot"
    assert pilot.monthly_price == 0
    assert pilot.is_active
