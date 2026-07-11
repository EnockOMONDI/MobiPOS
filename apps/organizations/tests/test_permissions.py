import pytest
from django.core.exceptions import PermissionDenied
from django.test import RequestFactory, override_settings
from django.contrib.auth.models import Permission

from apps.accounts.models import User
from apps.organizations.models import Branch, Company, Membership, MembershipStatus, Organization, Role
from apps.organizations.permissions import accessible_branches_for, organization_owner_required, organization_permission_required, user_has_organization_permission


@pytest.mark.django_db
def test_role_permission_is_scoped_to_active_membership():
    user = User.objects.create_user(username="manager", email="manager@example.com")
    organization = Organization.objects.create(name="Acme", slug="acme", status="active")
    membership = Membership.objects.create(
        user=user,
        organization=organization,
        status=MembershipStatus.ACTIVE,
    )
    permission = Permission.objects.get(
        content_type__app_label="organizations", codename="view_branch"
    )
    role = Role.objects.create(organization=organization, name="Manager", code="manager")
    role.permissions.add(permission)
    membership.roles.add(role)

    assert user_has_organization_permission(user, organization, "organizations.view_branch")
    assert not user_has_organization_permission(
        user, organization, "organizations.delete_branch"
    )


@pytest.mark.django_db
def test_role_permissions_are_cached_per_user_and_organization(django_assert_num_queries):
    user = User.objects.create_user(username="cached-manager", email="cached@example.com")
    organization = Organization.objects.create(name="Cached", slug="cached", status="active")
    membership = Membership.objects.create(
        user=user, organization=organization, status=MembershipStatus.ACTIVE,
    )
    permissions = Permission.objects.filter(
        content_type__app_label="organizations",
        codename__in=("view_branch", "add_branch"),
    )
    role = Role.objects.create(organization=organization, name="Manager", code="cached-manager")
    role.permissions.set(permissions)
    membership.roles.add(role)
    user._organization_permission_cache_enabled = True

    with django_assert_num_queries(5):
        assert user_has_organization_permission(user, organization, "organizations.view_branch")
    with django_assert_num_queries(0):
        assert user_has_organization_permission(user, organization, "organizations.add_branch")
        assert not user_has_organization_permission(user, organization, "organizations.delete_branch")


@pytest.mark.django_db
def test_non_owner_is_limited_to_assigned_branches():
    user = User.objects.create_user(username="branchstaff", email="branchstaff@example.com")
    organization = Organization.objects.create(name="Branch Org", slug="branch-org", status="active")
    company = Company.objects.create(organization=organization, name="Branch Co", code="BC")
    assigned = Branch.objects.create(organization=organization, company=company, name="Assigned", code="A")
    Branch.objects.create(organization=organization, company=company, name="Hidden", code="H")
    membership = Membership.objects.create(
        user=user, organization=organization, status=MembershipStatus.ACTIVE,
    )
    membership.branches.add(assigned)

    assert list(accessible_branches_for(user, organization)) == [assigned]


@pytest.mark.django_db
@override_settings(PRIVILEGED_OTP_REQUIRED=True)
def test_owner_privileged_action_requires_verified_otp():
    user = User.objects.create_user(username="otp-owner", email="otp-owner@example.com")
    organization = Organization.objects.create(name="OTP Org", slug="otp-org", status="active")
    membership = Membership.objects.create(
        user=user, organization=organization, status=MembershipStatus.ACTIVE, is_owner=True,
    )
    request = RequestFactory().post("/")
    request.user = user
    request.membership = membership

    protected = organization_owner_required(lambda request: "ok")

    response = protected(request)

    assert response.status_code == 302
    assert response.url.startswith("/accounts/mfa/verify/")


@pytest.mark.django_db
def test_operational_permission_decorator_denies_unassigned_member():
    user = User.objects.create_user(username="unassigned", email="unassigned@example.com")
    organization = Organization.objects.create(name="Permission Org", slug="permission-org", status="active")
    membership = Membership.objects.create(
        user=user, organization=organization, status=MembershipStatus.ACTIVE,
    )
    request = RequestFactory().post("/")
    request.user = user
    request.organization = organization
    request.membership = membership
    protected = organization_permission_required("sales.add_sale")(lambda request: "ok")

    with pytest.raises(PermissionDenied):
        protected(request)
