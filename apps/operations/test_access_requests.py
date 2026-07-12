import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.accounts.models import User
from apps.operations.models import ApprovalRequest, ApprovalStatus
from apps.organizations.models import Membership, MembershipStatus, Organization
from apps.organizations.permissions import user_has_organization_permission


@pytest.mark.django_db
def test_locked_navigation_stays_visible_and_register_is_protected(client):
    user = User.objects.create_user(username="staff", email="staff@example.com")
    organization = Organization.objects.create(name="Acme", slug="access-nav-acme", status="active")
    Membership.objects.create(
        user=user, organization=organization, status=MembershipStatus.ACTIVE,
    )
    client.force_login(user)

    dashboard = client.get(reverse("dashboard"))
    products = next(
        item
        for group in dashboard.context["navigation_groups"]
        for item in group["items"]
        if item["label"] == "Products"
    )

    assert products["enabled"] is False
    assert b"Products" in dashboard.content
    assert client.get(reverse("module-overview", args=["products"])).status_code == 403


@pytest.mark.django_db
def test_owner_approval_grants_requested_access_through_managed_role(client):
    organization = Organization.objects.create(name="Acme", slug="access-request-acme", status="active")
    staff = User.objects.create_user(username="staff", email="staff@example.com")
    owner = User.objects.create_user(username="owner", email="owner@example.com")
    Membership.objects.create(
        user=staff, organization=organization, status=MembershipStatus.ACTIVE,
    )
    Membership.objects.create(
        user=owner, organization=organization, status=MembershipStatus.ACTIVE, is_owner=True,
    )
    permission = Permission.objects.get(
        content_type__app_label="catalog", codename="view_product",
    )
    client.force_login(staff)

    response = client.post(reverse("access-request-create"), {
        "permission": "catalog.view_product",
        "reason": "I need to check pricing before serving customers.",
    })
    approval = ApprovalRequest.objects.get(requested_by=staff)

    assert response.status_code == 302
    assert approval.status == ApprovalStatus.PENDING
    assert not user_has_organization_permission(staff, organization, "catalog.view_product")

    client.force_login(owner)
    client.post(reverse("approval-decide", args=[approval.id]), {"decision": "approved"})

    approval.refresh_from_db()
    assert approval.status == ApprovalStatus.APPROVED
    assert user_has_organization_permission(staff, organization, "catalog.view_product")
    assert staff.memberships.get(organization=organization).roles.filter(permissions=permission).exists()


@pytest.mark.django_db
def test_repeated_access_request_preserves_decided_history(client):
    organization = Organization.objects.create(name="Acme", slug="access-history-acme", status="active")
    staff = User.objects.create_user(username="history-staff", email="history-staff@example.com")
    owner = User.objects.create_user(username="history-owner", email="history-owner@example.com")
    Membership.objects.create(user=staff, organization=organization, status=MembershipStatus.ACTIVE)
    Membership.objects.create(user=owner, organization=organization, status=MembershipStatus.ACTIVE, is_owner=True)
    client.force_login(staff)

    client.post(reverse("access-request-create"), {
        "permission": "catalog.view_product",
        "reason": "First request.",
    })
    first = ApprovalRequest.objects.get(requested_by=staff)
    client.force_login(owner)
    client.post(reverse("approval-decide", args=[first.id]), {
        "decision": "rejected",
        "decision_notes": "Need manager confirmation.",
    })
    first.refresh_from_db()

    client.force_login(staff)
    client.post(reverse("access-request-create"), {
        "permission": "catalog.view_product",
        "reason": "Second request with manager confirmation.",
    })

    requests = ApprovalRequest.objects.filter(requested_by=staff).order_by("created_at")
    assert requests.count() == 2
    second = requests.last()
    assert first.status == ApprovalStatus.REJECTED
    assert first.decision_notes == "Need manager confirmation."
    assert second.status == ApprovalStatus.PENDING
    assert second.previous_request_id == first.id
