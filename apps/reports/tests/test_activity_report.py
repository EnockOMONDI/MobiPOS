import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.services import record_audit_event
from apps.organizations.models import Membership, MembershipStatus, Organization


def _active_org(name, slug):
    return Organization.objects.create(name=name, slug=slug, status="active")


@pytest.mark.django_db
def test_owner_activity_report_includes_org_and_member_login_events(client):
    organization = _active_org("Acme Phones", "acme-phones")
    other_organization = _active_org("Other Phones", "other-phones")
    owner = User.objects.create_user(username="owner", email="owner@example.com", password="password")
    renny = User.objects.create_user(username="renny", email="renny@example.com", password="password")
    outsider = User.objects.create_user(username="outsider", email="outsider@example.com", password="password")
    Membership.objects.create(
        organization=organization, user=owner, status=MembershipStatus.ACTIVE, is_owner=True
    )
    Membership.objects.create(
        organization=organization, user=renny, status=MembershipStatus.ACTIVE
    )
    Membership.objects.create(
        organization=other_organization, user=outsider, status=MembershipStatus.ACTIVE, is_owner=True
    )
    record_audit_event(action="product.updated", actor=renny, organization=organization, message="Changed price")
    record_audit_event(action="auth.login", actor=renny)
    record_audit_event(action="product.updated", actor=outsider, organization=other_organization, message="Other tenant")

    client.force_login(owner)
    response = client.get(reverse("activity-report"))

    assert response.status_code == 200
    assert b"Product updated" in response.content
    assert b"User logged in" in response.content
    assert b"product.updated" in response.content
    assert b"auth.login" in response.content
    assert b"renny@example.com" in response.content
    assert b"Other tenant" not in response.content
    assert b"outsider@example.com" not in response.content


@pytest.mark.django_db
def test_non_owner_cannot_open_activity_report(client):
    organization = _active_org("Acme Phones", "acme-phones")
    user = User.objects.create_user(username="staff", email="staff@example.com", password="password")
    Membership.objects.create(organization=organization, user=user, status=MembershipStatus.ACTIVE)

    client.force_login(user)
    response = client.get(reverse("activity-report"))

    assert response.status_code == 403


@pytest.mark.django_db
def test_platform_admin_activity_report_is_platform_wide(client):
    organization = _active_org("Acme Phones", "acme-phones")
    admin = User.objects.create_user(
        username="platform", email="platform@example.com", password="password", is_platform_admin=True
    )
    actor = User.objects.create_user(username="renny", email="renny@example.com", password="password")
    record_audit_event(action="product.updated", actor=actor, organization=organization, message="Tenant event")
    record_audit_event(action="auth.login_failed", message="Authentication failed.")

    client.force_login(admin)
    response = client.get(reverse("activity-report"))

    assert response.status_code == 200
    assert b"Tenant event" in response.content
    assert b"Failed login attempt" in response.content
    assert b"auth.login_failed" in response.content
