import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.organizations.forms import RoleCreateForm
from apps.organizations.models import Branch, Company, Location, Membership, Organization, Role, Subscription


@pytest.mark.django_db
def test_owner_can_create_branch_scoped_user(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="mobipos-electronics")
    branch = Branch.objects.get(organization=organization)
    client.force_login(owner)

    response = client.post(reverse("tenant-user-create"), {
        "first_name": "New", "last_name": "Cashier", "username": "newcashier",
        "email": "newcashier@example.com", "password": "StrongPass123!",
        "branches": [branch.id],
    })

    membership = Membership.objects.get(organization=organization, user__username="newcashier")
    assert response.status_code == 302
    assert list(membership.branches.all()) == [branch]


@pytest.mark.django_db
def test_owner_branch_and_pos_creation_enforce_plan_limits(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="mobipos-electronics")
    company = Company.objects.get(organization=organization)
    subscription = Subscription.objects.get(organization=organization)
    subscription.plan.limits = {"users": 25, "branches": 2, "pos_locations": 2}
    subscription.plan.save(update_fields=["limits", "updated_at"])
    client.force_login(owner)

    response = client.post(reverse("branch-create"), {
        "company": company.id, "name": "Second Branch", "code": "SECOND",
    })
    branch = Branch.objects.get(organization=organization, code="SECOND")
    assert response.status_code == 302

    response = client.post(reverse("location-create"), {
        "branch": branch.id, "name": "Second POS", "code": "SECOND-POS", "location_type": "pos",
    })
    assert response.status_code == 302
    assert Location.objects.filter(organization=organization, location_type="pos").count() == 2

    response = client.post(reverse("branch-create"), {
        "company": company.id, "name": "Third Branch", "code": "THIRD",
    })
    assert response.status_code == 200
    assert not Branch.objects.filter(organization=organization, code="THIRD").exists()


@pytest.mark.django_db
def test_tenant_role_form_excludes_framework_and_privileged_permissions():
    app_labels = set(
        RoleCreateForm.base_fields["permissions"].queryset.values_list(
            "content_type__app_label", flat=True
        )
    )

    assert "auth" not in app_labels
    assert "audit" not in app_labels
    assert "organizations" not in app_labels
    assert "sales" in app_labels
    assert not RoleCreateForm.base_fields["permissions"].queryset.filter(
        codename__startswith="delete_"
    ).exists()


@pytest.mark.django_db
def test_owner_can_assign_multiple_roles_and_branches(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="alice")
    organization = Organization.objects.get(slug="mobipos-electronics")
    membership = Membership.objects.get(organization=organization, user=owner)
    first_role = Role.objects.create(organization=organization, name="Sales", code="sales-role")
    second_role = Role.objects.create(organization=organization, name="Inventory", code="inventory-role")
    branches = list(Branch.objects.filter(organization=organization))
    client.force_login(owner)

    response = client.post(reverse("membership-access-update", args=[membership.id]), {
        "roles": [first_role.id, second_role.id],
        "branches": [branch.id for branch in branches],
        "status": "active",
    })

    assert response.status_code == 302
    assert set(membership.roles.values_list("id", flat=True)) == {first_role.id, second_role.id}
    assert set(membership.branches.values_list("id", flat=True)) == {branch.id for branch in branches}
