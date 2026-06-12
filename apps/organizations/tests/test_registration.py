import pytest
from django.urls import reverse

from apps.organizations.models import Branch, Location, Membership, Organization, Plan, SubscriptionInvoice


@pytest.mark.django_db
def test_self_service_registration_creates_pending_tenant(client):
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
    assert organization.status == "pending"
    assert Membership.objects.get(organization=organization).is_owner
    assert Branch.objects.filter(organization=organization).exists()
    assert Location.objects.filter(organization=organization, location_type="pos").exists()
    assert SubscriptionInvoice.objects.get(organization=organization).amount == 1000
