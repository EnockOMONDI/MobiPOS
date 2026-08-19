from decimal import Decimal

import pytest
from django.core import mail
from django.test import RequestFactory, override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.operations.models import ApprovalPolicy, ApprovalRequest
from apps.operations.services import request_approval, user_can_decide_approval
from apps.organizations.context_processors import organization_context
from apps.organizations.models import Membership, MembershipStatus, Organization, Role
from apps.notifications.models import Notification


EMAIL_SETTINGS = {
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "DEFAULT_FROM_EMAIL": "MobiPOS <no-reply@example.com>",
    "APP_BASE_URL": "https://kipekeestudio.co.ke",
}


@pytest.mark.django_db
def test_navigation_approval_count_is_not_truncated_at_one_hundred():
    organization = Organization.objects.create(name="Approval Count Org", slug="approval-count-org", status="active")
    owner = User.objects.create_user(username="approval-count-owner", email="count-owner@example.com")
    requester = User.objects.create_user(username="approval-count-requester", email="count-requester@example.com")
    membership = Membership.objects.create(
        organization=organization,
        user=owner,
        status=MembershipStatus.ACTIVE,
        is_owner=True,
    )
    Membership.objects.create(organization=organization, user=requester, status=MembershipStatus.ACTIVE)
    ApprovalRequest.objects.bulk_create([
        ApprovalRequest(
            organization=organization,
            request_type="expense",
            target_type="tests.Target",
            target_id=str(index),
            reason="Count this pending approval.",
            requested_by=requester,
        )
        for index in range(101)
    ])
    request = RequestFactory().get("/")
    request.user = owner
    request.organization = organization
    request.membership = membership

    context = organization_context(request)

    assert context["pending_approval_count"] == 101


@pytest.mark.django_db
def test_policy_delegates_by_threshold_and_enforces_separation_of_duties():
    organization = Organization.objects.create(name="Policy Org", slug="policy-org", status="active")
    requester = User.objects.create_user(username="requester", email="requester@example.com")
    approver = User.objects.create_user(username="approver", email="approver@example.com")
    role = Role.objects.create(organization=organization, name="Finance Approver", code="finance-approver")
    requester_membership = Membership.objects.create(organization=organization, user=requester, status=MembershipStatus.ACTIVE)
    requester_membership.roles.add(role)
    approver_membership = Membership.objects.create(organization=organization, user=approver, status=MembershipStatus.ACTIVE)
    approver_membership.roles.add(role)
    policy = ApprovalPolicy.objects.create(
        organization=organization, name="Large credit", request_type="credit_sale",
        minimum_amount=Decimal("1000"), require_separate_approver=True,
    )
    policy.approver_roles.add(role)

    approval = request_approval(
        organization=organization, request_type="credit_sale", target=organization,
        requested_by=requester, reason="Large credit", amount=Decimal("1500"),
    )

    assert approval.policy == policy
    assert not user_can_decide_approval(user=requester, approval=approval)
    assert user_can_decide_approval(user=approver, approval=approval)


@pytest.mark.django_db
@override_settings(**EMAIL_SETTINGS)
def test_approval_request_notifies_eligible_owner(django_capture_on_commit_callbacks):
    organization = Organization.objects.create(name="Approval Notice Org", slug="approval-notice-org", status="active")
    requester = User.objects.create_user(username="approval-requester", email="requester@example.com")
    owner = User.objects.create_user(username="approval-owner", email="owner@example.com")
    Membership.objects.create(organization=organization, user=requester, status=MembershipStatus.ACTIVE)
    Membership.objects.create(organization=organization, user=owner, status=MembershipStatus.ACTIVE, is_owner=True)

    with django_capture_on_commit_callbacks(execute=True):
        approval = request_approval(
            organization=organization,
            request_type="aged_stock_action",
            target=organization,
            requested_by=requester,
            reason="Old stock needs transfer.",
        )

    notification = Notification.objects.get(organization=organization, recipient=owner)
    assert notification.link == reverse("approval-detail", args=[approval.id])
    assert notification.approval == approval
    assert notification.kind == Notification.Kind.APPROVAL_REQUEST
    assert "Aged-stock action approval" in notification.title
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["owner@example.com"]


@pytest.mark.django_db
def test_requester_can_open_own_approval_detail_read_only(client):
    organization = Organization.objects.create(name="Approval Read Org", slug="approval-read-org", status="active")
    requester = User.objects.create_user(username="approval-read-requester", email="requester@example.com")
    owner = User.objects.create_user(username="approval-read-owner", email="owner@example.com")
    Membership.objects.create(organization=organization, user=requester, status=MembershipStatus.ACTIVE)
    Membership.objects.create(organization=organization, user=owner, status=MembershipStatus.ACTIVE, is_owner=True)
    approval = request_approval(
        organization=organization,
        request_type="aged_stock_action",
        target=organization,
        requested_by=requester,
        reason="Old stock needs transfer.",
    )
    client.force_login(requester)

    response = client.get(reverse("approval-detail", args=[approval.id]))

    assert response.status_code == 200
    assert b"Waiting for approval" in response.content
    assert b'name="decision" value="approved"' not in response.content
