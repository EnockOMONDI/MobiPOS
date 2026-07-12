import pytest
from django.contrib.admin import AdminSite
from django.urls import reverse

from apps.accounts.models import User
from apps.contacts.models import Contact
from apps.organizations.admin import OrganizationOwnedAdmin
from apps.organizations.models import Branch, Company, Location, Membership, MembershipStatus, Organization
from apps.pos.models import POSSession
from apps.sales.models import Sale, SaleStatus


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


@pytest.mark.django_db
def test_tenant_admin_related_foreign_keys_are_scoped_to_owner_org(rf):
    first = Organization.objects.create(name="First Org", slug="first-admin-org", status="active")
    second = Organization.objects.create(name="Second Org", slug="second-admin-org", status="active")
    owner = User.objects.create_user(username="owner-admin", email="owner-admin@example.com", is_staff=True)
    Membership.objects.create(organization=first, user=owner, status=MembershipStatus.ACTIVE, is_owner=True)
    first_company = Company.objects.create(organization=first, name="First Co", code="FCO")
    second_company = Company.objects.create(organization=second, name="Second Co", code="SCO")
    request = rf.get("/admin/")
    request.user = owner
    model_admin = OrganizationOwnedAdmin(Branch, AdminSite())
    company_field = Branch._meta.get_field("company")

    form_field = model_admin.formfield_for_foreignkey(company_field, request)

    assert list(form_field.queryset) == [first_company]
    assert second_company not in form_field.queryset


@pytest.mark.django_db
def test_posted_transaction_is_read_only_in_admin(rf):
    organization = Organization.objects.create(name="Readonly Org", slug="readonly-admin-org", status="active")
    company = Company.objects.create(organization=organization, name="Company", code="CO")
    branch = Branch.objects.create(organization=organization, company=company, name="Branch", code="BR")
    location = Location.objects.create(organization=organization, branch=branch, name="POS", code="POS", location_type="pos")
    user = User.objects.create_user(username="admin-owner", email="admin-owner@example.com", is_staff=True)
    Membership.objects.create(organization=organization, user=user, status=MembershipStatus.ACTIVE, is_owner=True)
    session = POSSession.objects.create(organization=organization, number="S1", location=location, cashier=user)
    sale = Sale.objects.create(
        organization=organization, number="SALE1", session=session, location=location,
        status=SaleStatus.PAID, total=100, paid_total=100, created_by=user,
    )
    request = rf.get("/admin/")
    request.user = user
    model_admin = OrganizationOwnedAdmin(Sale, AdminSite())

    assert not model_admin.has_change_permission(request, sale)
    assert not model_admin.has_delete_permission(request, sale)
