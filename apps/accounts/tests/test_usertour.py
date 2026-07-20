import pytest
from django.core.management import call_command
from django.test import RequestFactory, override_settings
from django.urls import reverse

from apps.accounts.context_processors import build_usertour_payload
from apps.accounts.models import User
from apps.catalog.models import Product
from apps.contacts.models import Contact, ContactType
from apps.inventory.models import SerialStatus, StockUnit
from apps.organizations.models import Branch, Company, Membership, MembershipStatus, Organization, Role


def _tenant_user(*, is_demo_account=False):
    user = User.objects.create_user(
        username="tenant-user",
        email="tenant@example.com",
        password="StrongPass123!",
        first_name="Tenant",
        is_demo_account=is_demo_account,
    )
    organization = Organization.objects.create(name="Sensitive Client Ltd", slug="sensitive-client", status="active")
    company = Company.objects.create(organization=organization, name="Sensitive Company", code="SCL")
    branch = Branch.objects.create(organization=organization, company=company, name="Sensitive Branch", code="SBR")
    role = Role.objects.create(organization=organization, name="Sensitive Role", code="sensitive-role")
    membership = Membership.objects.create(
        organization=organization,
        user=user,
        status=MembershipStatus.ACTIVE,
        is_owner=True,
    )
    membership.roles.add(role)
    membership.branches.add(branch)
    return user, organization, membership


@pytest.mark.django_db
@override_settings(USERTOUR_ENABLED=True, USERTOUR_TOKEN="test-token", USERTOUR_DEMO_ONLY=True)
def test_usertour_payload_is_demo_only():
    user, organization, membership = _tenant_user(is_demo_account=False)
    request = RequestFactory().get("/")
    request.user = user
    request.organization = organization
    request.membership = membership

    assert build_usertour_payload(request) is None


@pytest.mark.django_db
@override_settings(USERTOUR_ENABLED=True, USERTOUR_TOKEN="test-token", USERTOUR_DEMO_ONLY=True)
def test_usertour_payload_uses_only_privacy_allowlist():
    user, organization, membership = _tenant_user(is_demo_account=True)
    request = RequestFactory().get("/pos/")
    request.user = user
    request.organization = organization
    request.membership = membership
    request.resolver_match = None

    payload = build_usertour_payload(request)

    assert payload == {
        "user_id": str(user.id),
        "display_name": "Tenant Demo",
        "account_type": "demo",
        "organization_id": str(organization.id),
        "organization_name": "Sensitive Client Ltd",
        "branch_id": str(membership.branches.first().id),
        "branch_name": "Sensitive Branch Demo",
        "roles": ["Sensitive Role"],
        "is_owner": True,
        "module": "",
    }
    assert set(payload) == {
        "user_id",
        "display_name",
        "account_type",
        "organization_id",
        "organization_name",
        "branch_id",
        "branch_name",
        "roles",
        "is_owner",
        "module",
    }


@pytest.mark.django_db
@override_settings(USERTOUR_ENABLED=False, USERTOUR_TOKEN="test-token", USERTOUR_DEMO_ONLY=True)
def test_usertour_does_not_render_when_disabled(client):
    call_command("seed_demo_data")
    client.force_login(User.objects.get(username="brian"))

    response = client.get(reverse("dashboard"))

    assert response.status_code == 200
    assert b"js.usertour.io" not in response.content
    assert b"usertour-payload" not in response.content


@pytest.mark.django_db
@override_settings(USERTOUR_ENABLED=True, USERTOUR_TOKEN="test-token", USERTOUR_DEMO_ONLY=True)
def test_usertour_renders_for_seeded_demo_accounts(client):
    call_command("seed_demo_data")
    brian = User.objects.get(username="brian")

    assert brian.is_demo_account
    client.force_login(brian)
    response = client.get(reverse("dashboard"))

    assert response.status_code == 200
    assert b"js.usertour.io/" in response.content
    assert b"legacy/usertour.iife.js" in response.content
    assert b"usertour-payload" in response.content
    assert b"Brian Demo" in response.content
    assert b"Nairobi Mobile Hub" in response.content


@pytest.mark.django_db
@override_settings(USERTOUR_ENABLED=True, USERTOUR_TOKEN="test-token", USERTOUR_DEMO_ONLY=True)
def test_usertour_never_renders_for_live_client_accounts(client):
    user, _, _ = _tenant_user(is_demo_account=False)
    client.force_login(user)

    response = client.get(reverse("dashboard"))

    assert response.status_code == 200
    assert b"js.usertour.io" not in response.content
    assert b"usertour-payload" not in response.content


@pytest.mark.django_db
@override_settings(USERTOUR_ENABLED=True, USERTOUR_TOKEN="test-token", USERTOUR_DEMO_ONLY=True)
def test_usertour_rendered_payload_does_not_include_operational_sensitive_data(client):
    call_command("seed_demo_data")
    brian = User.objects.get(username="brian")
    organization = brian.memberships.get().organization
    StockUnit.objects.create(
        organization=organization,
        product=Product.objects.filter(organization=organization, is_serialized=True).first(),
        serial_number="359999999999999",
        status=SerialStatus.AVAILABLE,
    )
    Contact.objects.create(
        organization=organization,
        contact_type=ContactType.CUSTOMER,
        name="Private Customer",
        phone_number="+254799999999",
        credit_limit=50000,
    )
    client.force_login(brian)

    response = client.get(reverse("dashboard"))

    assert response.status_code == 200
    assert b"js.usertour.io/" in response.content
    assert b"legacy/usertour.iife.js" in response.content
    assert b"359999999999999" not in response.content
    assert b"+254799999999" not in response.content
    assert b"Private Customer" not in response.content
    assert b"credit_limit" not in response.content
