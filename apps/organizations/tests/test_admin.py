import pytest
from django.contrib.admin import AdminSite
from django.urls import reverse

from apps.accounts.models import User
from apps.contacts.models import Contact
from apps.organizations.admin import OrganizationOwnedAdmin
from apps.organizations.models import Membership, MembershipStatus, Organization


@pytest.mark.django_db
def test_admin_login_page_supports_anonymous_user(client):
    response = client.get(reverse("admin:login"))

    assert response.status_code == 200
    assert b"MobiPOS" in response.content


@pytest.mark.django_db
def test_tenant_admin_change_requires_owner(rf):
    organization = Organization.objects.create(name="Admin Org", slug="admin-org", status="active")
    member = User.objects.create_user(username="admin-member", email="admin-member@example.com", is_staff=True)
    Membership.objects.create(organization=organization, user=member, status=MembershipStatus.ACTIVE)
    contact = Contact.objects.create(organization=organization, contact_type="customer", name="Customer")
    request = rf.get("/admin/")
    request.user = member
    model_admin = OrganizationOwnedAdmin(Contact, AdminSite())

    assert model_admin.has_view_permission(request, contact)
    assert not model_admin.has_change_permission(request, contact)
    assert not model_admin.has_delete_permission(request, contact)
