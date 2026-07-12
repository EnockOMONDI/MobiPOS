import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError

from apps.accounts.models import User
from apps.organizations.models import (
    Branch,
    Company,
    Membership,
    MembershipStatus,
    Location,
    LocationType,
    Organization,
)


@pytest.mark.django_db
def test_membership_is_unique_per_user_and_organization():
    user = User.objects.create_user(username="owner", email="owner@example.com")
    organization = Organization.objects.create(name="Acme", slug="acme")
    Membership.objects.create(
        user=user,
        organization=organization,
        status=MembershipStatus.ACTIVE,
    )

    with pytest.raises(IntegrityError):
        Membership.objects.create(
            user=user,
            organization=organization,
            status=MembershipStatus.ACTIVE,
        )


@pytest.mark.django_db
def test_branch_rejects_company_from_another_organization():
    first = Organization.objects.create(name="First", slug="first")
    second = Organization.objects.create(name="Second", slug="second")
    company = Company.objects.create(organization=first, name="First Co", code="FIRST")
    branch = Branch(organization=second, company=company, name="Wrong", code="WRONG")

    with pytest.raises(ValidationError):
        branch.full_clean()


@pytest.mark.django_db
def test_organization_owned_models_reject_cross_tenant_foreign_keys():
    from apps.contacts.models import Contact
    from apps.purchasing.models import PurchaseOrder

    user = User.objects.create_user(username="tenant-check", email="tenant-check@example.com")
    first = Organization.objects.create(name="First Validation", slug="first-validation")
    second = Organization.objects.create(name="Second Validation", slug="second-validation")
    company = Company.objects.create(organization=first, name="First Co", code="FIRST")
    branch = Branch.objects.create(organization=first, company=company, name="First Branch", code="FBR")
    location = Location.objects.create(
        organization=first, branch=branch, name="First Warehouse", code="FWH", location_type="warehouse"
    )
    supplier = Contact.objects.create(organization=second, contact_type="supplier", name="Wrong Supplier")
    order = PurchaseOrder(
        organization=first, number="PO-CROSS", supplier=supplier, destination=location,
        ordered_on="2026-06-12", created_by=user,
    )

    with pytest.raises(ValidationError):
        order.full_clean()


@pytest.mark.django_db
def test_agent_location_requires_branch_assigned_custodian():
    organization = Organization.objects.create(name="Agent Org", slug="agent-org", status="active")
    company = Company.objects.create(organization=organization, name="Company", code="CO")
    assigned = Branch.objects.create(organization=organization, company=company, name="Assigned", code="A")
    hidden = Branch.objects.create(organization=organization, company=company, name="Hidden", code="H")
    user = User.objects.create_user(username="agent-user", email="agent@example.com")
    membership = Membership.objects.create(organization=organization, user=user, status=MembershipStatus.ACTIVE)
    membership.branches.add(assigned)

    valid = Location(
        organization=organization,
        branch=assigned,
        name="Agent User Stock",
        code="AGENT-1",
        location_type=LocationType.AGENT,
        custodian_membership=membership,
    )
    valid.full_clean()

    invalid = Location(
        organization=organization,
        branch=hidden,
        name="Wrong Branch",
        code="AGENT-2",
        location_type=LocationType.AGENT,
        custodian_membership=membership,
    )
    with pytest.raises(ValidationError):
        invalid.full_clean()
