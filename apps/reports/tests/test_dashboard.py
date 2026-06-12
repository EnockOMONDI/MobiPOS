import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.organizations.models import Membership, MembershipStatus, Organization
from apps.catalog.models import Category, Product


@pytest.mark.django_db
def test_dashboard_requires_authentication(client):
    response = client.get(reverse("dashboard"))

    assert response.status_code == 302
    assert reverse("login") in response.url


@pytest.mark.django_db
def test_dashboard_resolves_active_organization(client):
    user = User.objects.create_user(
        username="owner", email="owner@example.com", password="password"
    )
    organization = Organization.objects.create(name="Acme", slug="acme", status="active")
    Membership.objects.create(
        user=user,
        organization=organization,
        status=MembershipStatus.ACTIVE,
        is_owner=True,
    )
    client.force_login(user)

    response = client.get(reverse("dashboard"))

    assert response.status_code == 200
    assert response.context["active_organization"] == organization


@pytest.mark.django_db
def test_module_overview_is_tenant_scoped(client):
    user = User.objects.create_user(username="owner", email="scope@example.com")
    first = Organization.objects.create(name="First", slug="first-scope", status="active")
    second = Organization.objects.create(name="Second", slug="second-scope", status="active")
    Membership.objects.create(user=user, organization=first, status=MembershipStatus.ACTIVE, is_owner=True)
    first_category = Category.objects.create(organization=first, name="Phones", code="phones")
    second_category = Category.objects.create(organization=second, name="Phones", code="phones")
    Product.objects.create(organization=first, category=first_category, name="Visible Product", sku="VISIBLE")
    Product.objects.create(organization=second, category=second_category, name="Hidden Product", sku="HIDDEN")
    client.force_login(user)

    response = client.get(reverse("module-overview", args=["products"]))

    assert b"Visible Product" in response.content
    assert b"Hidden Product" not in response.content
