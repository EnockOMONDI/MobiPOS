from decimal import Decimal

import pytest

from apps.accounts.models import User
from apps.operations.models import ApprovalPolicy
from apps.operations.services import request_approval, user_can_decide_approval
from apps.organizations.models import Membership, MembershipStatus, Organization, Role


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
