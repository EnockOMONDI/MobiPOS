import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.models import AuditEvent
from apps.organizations.forms import RoleCreateForm
from apps.organizations.models import AgentDocument, AgentDocumentType, AgentProfile, AgentProfileStatus, AgentProfileType, Branch, Company, Location, LocationType, Membership, MembershipStatus, Organization, OrganizationSetting, Role, Subscription


@pytest.mark.django_db
def test_owner_can_create_branch_scoped_user(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    branch = Branch.objects.get(organization=organization, code="WST")
    role = Role.objects.get(organization=organization, code="cashier")
    client.force_login(owner)

    response = client.post(reverse("tenant-user-create"), {
        "first_name": "New", "last_name": "Cashier", "username": "newcashier",
        "email": "newcashier@example.com", "password": "StrongPass123!", "password_confirm": "StrongPass123!",
        "branches": [branch.id],
        "roles": [role.id],
    })

    membership = Membership.objects.get(organization=organization, user__username="newcashier")
    assert response.status_code == 302
    assert membership.status == MembershipStatus.ACTIVE
    assert membership.user.is_active
    assert membership.user.check_password("StrongPass123!")
    assert list(membership.branches.all()) == [branch]
    assert list(membership.roles.all()) == [role]


@pytest.mark.django_db
def test_user_create_page_links_to_branch_and_role_setup(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    client.force_login(owner)

    response = client.get(reverse("tenant-user-create"))

    assert response.status_code == 200
    assert b"Add branch" in response.content
    assert reverse("branch-create") in response.content.decode()
    assert b"Add role" in response.content
    assert reverse("role-create") in response.content.decode()


@pytest.mark.django_db
def test_owner_can_create_user_with_agent_stock_custody_location(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    branch = Branch.objects.get(organization=organization, code="WST")
    client.force_login(owner)

    response = client.post(reverse("tenant-user-create"), {
        "first_name": "Brian",
        "last_name": "Agent",
        "username": "brianagent",
        "email": "brian.agent@example.com",
        "password": "StrongPass123!",
        "password_confirm": "StrongPass123!",
        "branches": [branch.id],
        "enable_stock_custody": "on",
        "custody_branch": branch.id,
    })

    membership = Membership.objects.get(organization=organization, user__email="brian.agent@example.com")
    custody_location = Location.objects.get(
        organization=organization,
        custodian_membership=membership,
        location_type=LocationType.AGENT,
        is_active=True,
    )
    assert response.status_code == 302
    assert custody_location.branch == branch
    assert custody_location.name == "Brian Agent Stock Custody"


@pytest.mark.django_db
def test_owner_can_create_agent_profile_during_user_invite(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    branch = Branch.objects.get(organization=organization, code="WST")
    client.force_login(owner)

    response = client.post(reverse("tenant-user-create"), {
        "first_name": "Mary",
        "last_name": "Agent",
        "username": "maryagent",
        "email": "mary.agent@example.com",
        "phone_number": "+254700999001",
        "password": "StrongPass123!",
        "password_confirm": "StrongPass123!",
        "branches": [branch.id],
        "create_agent_profile": "on",
        "agent_profile_type": AgentProfileType.AGENT,
        "agent_branch": branch.id,
        "legal_name": "Mary Agent",
        "national_id_number": "ID-MARY-001",
        "registration_notes": "Field onboarding",
    })

    membership = Membership.objects.get(organization=organization, user__email="mary.agent@example.com")
    profile = AgentProfile.objects.get(organization=organization, membership=membership)
    assert response.status_code == 302
    assert profile.profile_type == AgentProfileType.AGENT
    assert profile.status == AgentProfileStatus.PENDING
    assert profile.branch == branch
    assert profile.legal_name == "Mary Agent"
    assert profile.national_id_number == "ID-MARY-001"
    assert profile.registered_by == owner


@pytest.mark.django_db
def test_owner_can_create_dsa_profile_with_supervising_agent(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    branch = Branch.objects.get(organization=organization, code="WST")
    supervisor_user = User.objects.create_user(username="super-agent", email="super-agent@example.com")
    supervisor_membership = Membership.objects.create(
        organization=organization,
        user=supervisor_user,
        status=MembershipStatus.ACTIVE,
    )
    supervisor_membership.branches.add(branch)
    supervisor = AgentProfile.objects.create(
        organization=organization,
        membership=supervisor_membership,
        profile_type=AgentProfileType.AGENT,
        status=AgentProfileStatus.ACTIVE,
        branch=branch,
        legal_name="Super Agent",
    )
    client.force_login(owner)

    response = client.post(reverse("tenant-user-create"), {
        "first_name": "Jane",
        "last_name": "DSA",
        "username": "janedsa",
        "email": "jane.dsa@example.com",
        "password": "StrongPass123!",
        "password_confirm": "StrongPass123!",
        "branches": [branch.id],
        "create_agent_profile": "on",
        "agent_profile_type": AgentProfileType.DSA,
        "agent_branch": branch.id,
        "agent_supervisor": supervisor.id,
        "legal_name": "Jane DSA",
    })

    profile = AgentProfile.objects.get(organization=organization, membership__user__email="jane.dsa@example.com")
    assert response.status_code == 302
    assert profile.profile_type == AgentProfileType.DSA
    assert profile.supervisor == supervisor


@pytest.mark.django_db
def test_owner_branch_and_pos_creation_enforce_plan_limits(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    company = Company.objects.filter(organization=organization).order_by("code").first()
    subscription = Subscription.objects.get(organization=organization)
    branch_limit = Branch.objects.filter(organization=organization).count() + 1
    pos_limit = Location.objects.filter(organization=organization, location_type="pos").count() + 1
    subscription.plan.limits = {"users": 25, "branches": branch_limit, "pos_locations": pos_limit}
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
    assert Location.objects.filter(organization=organization, location_type="pos").count() == pos_limit

    response = client.post(reverse("branch-create"), {
        "company": company.id, "name": "Third Branch", "code": "THIRD",
    })
    assert response.status_code == 200
    assert not Branch.objects.filter(organization=organization, code="THIRD").exists()


@pytest.mark.django_db
def test_branch_and_location_setup_forms_preselect_single_parent_and_return_to_purchase(client):
    organization = Organization.objects.create(name="Setup Flow", slug="setup-flow", status="active")
    owner = User.objects.create_user(username="setup-flow-owner", email="setup-flow-owner@example.com")
    company = Company.objects.create(organization=organization, name="Setup Flow Company", code="SETUP")
    branch = Branch.objects.create(organization=organization, company=company, name="Main Branch", code="MAIN")
    membership = Membership.objects.create(
        organization=organization,
        user=owner,
        status=MembershipStatus.ACTIVE,
        is_owner=True,
    )
    membership.branches.add(branch)
    client.force_login(owner)

    branch_response = client.get(reverse("branch-create"))
    location_response = client.get(reverse("location-create"))
    create_response = client.post(reverse("location-create"), {
        "branch": branch.id,
        "name": "Return Warehouse",
        "code": "RETURN-WH",
        "location_type": LocationType.WAREHOUSE,
        "next": reverse("purchase-create"),
    })

    assert branch_response.status_code == 200
    assert f'<option value="{company.id}" selected>'.encode() in branch_response.content
    assert location_response.status_code == 200
    assert f'<option value="{branch.id}" selected>'.encode() in location_response.content
    assert create_response.status_code == 302
    assert create_response.url == reverse("purchase-create")
    assert Location.objects.filter(organization=organization, code="RETURN-WH").exists()


@pytest.mark.django_db
def test_tenant_role_form_excludes_framework_and_privileged_permissions():
    queryset = RoleCreateForm.base_fields["permissions"].queryset
    app_labels = set(queryset.values_list("content_type__app_label", flat=True))

    assert "auth" not in app_labels
    assert "audit" not in app_labels
    assert "sales" in app_labels
    assert not queryset.filter(codename__startswith="delete_").exists()
    assert set(queryset.filter(content_type__app_label="organizations").values_list("codename", flat=True)) == {
        "add_agentprofile",
        "view_activity_report",
        "view_operational_report",
        "view_retail_analytics",
    }


@pytest.mark.django_db
def test_owner_can_assign_multiple_roles_and_branches(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
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


@pytest.mark.django_db
def test_owner_can_provision_agent_stock_custody_from_access_update(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    branch = Branch.objects.get(organization=organization, code="WST")
    staff = User.objects.create_user(username="fieldagent", email="fieldagent@example.com", first_name="Field", last_name="Agent")
    membership = Membership.objects.create(
        organization=organization,
        user=staff,
        status=MembershipStatus.ACTIVE,
    )
    membership.branches.add(branch)
    client.force_login(owner)

    response = client.post(reverse("membership-access-update", args=[membership.id]), {
        "branches": [branch.id],
        "roles": [],
        "status": MembershipStatus.ACTIVE,
        "enable_stock_custody": "on",
        "custody_branch": branch.id,
    })

    custody_location = Location.objects.get(
        organization=organization,
        custodian_membership=membership,
        location_type=LocationType.AGENT,
        is_active=True,
    )
    assert response.status_code == 302
    assert custody_location.branch == branch
    assert custody_location.name == "Field Agent Stock Custody"


@pytest.mark.django_db
def test_owner_can_update_agent_profile_from_access_update(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    branch = Branch.objects.get(organization=organization, code="WST")
    staff = User.objects.create_user(username="profile-update-agent", email="profile-update-agent@example.com", first_name="Profile", last_name="Agent")
    membership = Membership.objects.create(
        organization=organization,
        user=staff,
        status=MembershipStatus.ACTIVE,
    )
    membership.branches.add(branch)
    client.force_login(owner)

    response = client.post(reverse("membership-access-update", args=[membership.id]), {
        "branches": [branch.id],
        "roles": [],
        "status": MembershipStatus.ACTIVE,
        "create_agent_profile": "on",
        "agent_profile_type": AgentProfileType.AGENT,
        "agent_branch": branch.id,
        "agent_status": AgentProfileStatus.ACTIVE,
        "legal_name": "Profile Agent",
        "national_id_number": "ID-PROFILE-001",
        "registration_notes": "Verified by owner",
    })

    profile = AgentProfile.objects.get(organization=organization, membership=membership)
    assert response.status_code == 302
    assert profile.status == AgentProfileStatus.ACTIVE
    assert profile.legal_name == "Profile Agent"
    assert profile.national_id_number == "ID-PROFILE-001"


@pytest.mark.django_db
def test_agent_register_and_detail_are_owner_visible(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    branch = Branch.objects.get(organization=organization, code="WST")
    membership = Membership.objects.get(organization=organization, user=owner)
    profile = AgentProfile.objects.create(
        organization=organization,
        membership=membership,
        profile_type=AgentProfileType.AGENT,
        status=AgentProfileStatus.ACTIVE,
        branch=branch,
        legal_name="Owner Agent Profile",
    )
    client.force_login(owner)

    register = client.get(reverse("module-overview", args=["agents"]))
    detail = client.get(reverse("agent-profile-detail", args=[profile.id]))

    assert register.status_code == 200
    assert b"Owner Agent Profile" in register.content
    assert detail.status_code == 200
    assert b"Owner Agent Profile" in detail.content
    assert b"Agent network" in detail.content


@pytest.mark.django_db
def test_owner_can_upload_and_download_agent_document(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    branch = Branch.objects.get(organization=organization, code="WST")
    membership = Membership.objects.get(organization=organization, user=owner)
    profile = AgentProfile.objects.create(
        organization=organization,
        membership=membership,
        profile_type=AgentProfileType.AGENT,
        status=AgentProfileStatus.ACTIVE,
        branch=branch,
        legal_name="Documented Agent",
    )
    upload = SimpleUploadedFile(
        "national-id.pdf",
        b"%PDF-1.4\nfake-id",
        content_type="application/pdf",
    )
    client.force_login(owner)

    response = client.post(reverse("agent-document-upload", args=[profile.id]), {
        "document_type": AgentDocumentType.NATIONAL_ID,
        "file": upload,
        "notes": "Scanned ID",
    })

    document = AgentDocument.objects.get(organization=organization, profile=profile)
    download = client.get(reverse("agent-document-download", args=[document.id]))

    assert response.status_code == 302
    assert document.original_filename == "national-id.pdf"
    assert document.uploaded_by == owner
    assert document.notes == "Scanned ID"
    assert download.status_code == 200
    assert AuditEvent.objects.filter(organization=organization, action="agent_document.uploaded", target_id=str(document.id)).exists()
    assert AuditEvent.objects.filter(organization=organization, action="agent_document.downloaded", target_id=str(document.id)).exists()


@pytest.mark.django_db
def test_non_owner_cannot_upload_agent_document(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    branch = Branch.objects.get(organization=organization, code="WST")
    owner_membership = Membership.objects.get(organization=organization, user=owner)
    profile = AgentProfile.objects.create(
        organization=organization,
        membership=owner_membership,
        profile_type=AgentProfileType.AGENT,
        status=AgentProfileStatus.ACTIVE,
        branch=branch,
        legal_name="Protected Agent",
    )
    staff = User.objects.create_user(username="agent-doc-staff", email="agent-doc-staff@example.com")
    staff_membership = Membership.objects.create(
        organization=organization,
        user=staff,
        status=MembershipStatus.ACTIVE,
    )
    staff_membership.branches.add(branch)
    upload = SimpleUploadedFile(
        "blocked.pdf",
        b"%PDF-1.4\nblocked",
        content_type="application/pdf",
    )
    client.force_login(staff)

    response = client.post(reverse("agent-document-upload", args=[profile.id]), {
        "document_type": AgentDocumentType.NATIONAL_ID,
        "file": upload,
    })

    assert response.status_code == 403
    assert not AgentDocument.objects.filter(organization=organization, profile=profile).exists()


@pytest.mark.django_db
def test_non_owner_cannot_download_agent_document(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    branch = Branch.objects.get(organization=organization, code="WST")
    owner_membership = Membership.objects.get(organization=organization, user=owner)
    profile = AgentProfile.objects.create(
        organization=organization,
        membership=owner_membership,
        profile_type=AgentProfileType.AGENT,
        status=AgentProfileStatus.ACTIVE,
        branch=branch,
        legal_name="Download Protected Agent",
    )
    document = AgentDocument.objects.create(
        organization=organization,
        profile=profile,
        document_type=AgentDocumentType.NATIONAL_ID,
        file=SimpleUploadedFile("protected-id.pdf", b"%PDF-1.4\nprotected", content_type="application/pdf"),
        original_filename="protected-id.pdf",
        content_type="application/pdf",
        size=18,
        uploaded_by=owner,
    )
    staff = User.objects.create_user(username="agent-doc-download-staff", email="agent-doc-download-staff@example.com")
    staff_membership = Membership.objects.create(
        organization=organization,
        user=staff,
        status=MembershipStatus.ACTIVE,
    )
    staff_membership.branches.add(branch)
    client.force_login(staff)

    response = client.get(reverse("agent-document-download", args=[document.id]))

    assert response.status_code == 403


@pytest.mark.django_db
def test_owner_can_approve_suspend_and_reactivate_agent_profile(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    branch = Branch.objects.get(organization=organization, code="WST")
    membership = Membership.objects.get(organization=organization, user=owner)
    profile = AgentProfile.objects.create(
        organization=organization,
        membership=membership,
        profile_type=AgentProfileType.AGENT,
        status=AgentProfileStatus.PENDING,
        branch=branch,
        legal_name="Approval Agent",
    )
    client.force_login(owner)

    approve = client.post(reverse("agent-profile-decide", args=[profile.id, "approve"]), {"notes": "ID checked"})
    profile.refresh_from_db()
    assert approve.status_code == 302
    assert profile.status == AgentProfileStatus.ACTIVE
    assert profile.verified_by == owner
    assert profile.verified_at is not None
    assert profile.verification_notes == "ID checked"

    suspend = client.post(reverse("agent-profile-decide", args=[profile.id, "suspend"]), {"notes": "Compliance review"})
    profile.refresh_from_db()
    assert suspend.status_code == 302
    assert profile.status == AgentProfileStatus.SUSPENDED
    assert profile.verification_notes == "Compliance review"

    reactivate = client.post(reverse("agent-profile-decide", args=[profile.id, "reactivate"]), {"notes": "Review complete"})
    profile.refresh_from_db()
    assert reactivate.status_code == 302
    assert profile.status == AgentProfileStatus.ACTIVE
    assert profile.verification_notes == "Review complete"
    assert AuditEvent.objects.filter(organization=organization, action="agent_profile.approve", target_id=str(profile.id)).exists()
    assert AuditEvent.objects.filter(organization=organization, action="agent_profile.suspend", target_id=str(profile.id)).exists()
    assert AuditEvent.objects.filter(organization=organization, action="agent_profile.reactivate", target_id=str(profile.id)).exists()


@pytest.mark.django_db
def test_owner_can_reject_pending_agent_profile(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    branch = Branch.objects.get(organization=organization, code="WST")
    membership = Membership.objects.get(organization=organization, user=owner)
    profile = AgentProfile.objects.create(
        organization=organization,
        membership=membership,
        profile_type=AgentProfileType.AGENT,
        status=AgentProfileStatus.PENDING,
        branch=branch,
        legal_name="Rejected Agent",
    )
    client.force_login(owner)

    response = client.post(reverse("agent-profile-decide", args=[profile.id, "reject"]), {"notes": "ID mismatch"})
    profile.refresh_from_db()

    assert response.status_code == 302
    assert profile.status == AgentProfileStatus.REJECTED
    assert profile.verified_by == owner
    assert profile.verification_notes == "ID mismatch"
    assert AuditEvent.objects.filter(organization=organization, action="agent_profile.reject", target_id=str(profile.id)).exists()


@pytest.mark.django_db
def test_invalid_agent_profile_decision_does_not_change_status(client):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    branch = Branch.objects.get(organization=organization, code="WST")
    membership = Membership.objects.get(organization=organization, user=owner)
    profile = AgentProfile.objects.create(
        organization=organization,
        membership=membership,
        profile_type=AgentProfileType.AGENT,
        status=AgentProfileStatus.PENDING,
        branch=branch,
        legal_name="Invalid Transition Agent",
    )
    client.force_login(owner)

    response = client.post(reverse("agent-profile-decide", args=[profile.id, "suspend"]), {"notes": "Too early"})
    profile.refresh_from_db()

    assert response.status_code == 302
    assert profile.status == AgentProfileStatus.PENDING
    assert not AuditEvent.objects.filter(organization=organization, action="agent_profile.suspend", target_id=str(profile.id)).exists()


@pytest.mark.django_db
def test_active_agent_can_register_dsa_when_policy_enabled(client):
    call_command("seed_demo_data")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    branch = Branch.objects.get(organization=organization, code="WST")
    agent_user = User.objects.create_user(
        username="self-onboard-agent",
        email="self-onboard-agent@example.com",
        first_name="Self",
        last_name="Agent",
    )
    agent_membership = Membership.objects.create(
        organization=organization,
        user=agent_user,
        status=MembershipStatus.ACTIVE,
    )
    agent_membership.branches.add(branch)
    role = Role.objects.create(organization=organization, name="Agent onboarding", code="agent-onboarding")
    role.permissions.add(Permission.objects.get(content_type__app_label="organizations", codename="add_agentprofile"))
    agent_membership.roles.add(role)
    supervisor = AgentProfile.objects.create(
        organization=organization,
        membership=agent_membership,
        profile_type=AgentProfileType.AGENT,
        status=AgentProfileStatus.ACTIVE,
        branch=branch,
        legal_name="Self Agent",
    )
    OrganizationSetting.objects.create(
        organization=organization,
        key="allow_agent_dsa_registration",
        value={"enabled": True},
    )
    client.force_login(agent_user)

    response = client.post(reverse("agent-dsa-create"), {
        "first_name": "Field",
        "last_name": "DSA",
        "username": "field-dsa",
        "email": "field-dsa@example.com",
        "phone_number": "+254700123123",
        "branch": branch.id,
        "legal_name": "Field DSA",
        "national_id_number": "ID-FIELD-DSA",
        "registration_notes": "Registered in the field",
    })

    profile = AgentProfile.objects.get(organization=organization, membership__user__username="field-dsa")
    assert response.status_code == 302
    assert profile.profile_type == AgentProfileType.DSA
    assert profile.status == AgentProfileStatus.PENDING
    assert profile.supervisor == supervisor
    assert profile.branch == branch
    assert profile.registered_by == agent_user
    assert profile.membership.status == MembershipStatus.INVITED
    assert profile.membership.invitation.invited_by == agent_user
    assert AuditEvent.objects.filter(organization=organization, action="agent_profile.dsa_registered", target_id=str(profile.id)).exists()


@pytest.mark.django_db
def test_agent_cannot_register_dsa_when_policy_disabled(client):
    call_command("seed_demo_data")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    branch = Branch.objects.get(organization=organization, code="WST")
    agent_user = User.objects.create_user(username="blocked-agent", email="blocked-agent@example.com")
    agent_membership = Membership.objects.create(
        organization=organization,
        user=agent_user,
        status=MembershipStatus.ACTIVE,
    )
    agent_membership.branches.add(branch)
    role = Role.objects.create(organization=organization, name="Blocked onboarding", code="blocked-onboarding")
    role.permissions.add(Permission.objects.get(content_type__app_label="organizations", codename="add_agentprofile"))
    agent_membership.roles.add(role)
    AgentProfile.objects.create(
        organization=organization,
        membership=agent_membership,
        profile_type=AgentProfileType.AGENT,
        status=AgentProfileStatus.ACTIVE,
        branch=branch,
        legal_name="Blocked Agent",
    )
    client.force_login(agent_user)

    response = client.post(reverse("agent-dsa-create"), {
        "first_name": "Blocked",
        "last_name": "DSA",
        "username": "blocked-dsa",
        "email": "blocked-dsa@example.com",
        "branch": branch.id,
    })

    assert response.status_code == 403
    assert not User.objects.filter(username="blocked-dsa").exists()


@pytest.mark.django_db
def test_agent_dsa_registration_rejects_unassigned_branch(client):
    call_command("seed_demo_data")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    company = Company.objects.filter(organization=organization).order_by("code").first()
    assigned_branch = Branch.objects.get(organization=organization, code="WST")
    unassigned_branch = Branch.objects.create(
        organization=organization,
        company=company,
        name="Unassigned Branch",
        code="UNASSIGNED",
    )
    agent_user = User.objects.create_user(username="branch-agent", email="branch-agent@example.com")
    agent_membership = Membership.objects.create(
        organization=organization,
        user=agent_user,
        status=MembershipStatus.ACTIVE,
    )
    agent_membership.branches.add(assigned_branch)
    role = Role.objects.create(organization=organization, name="Branch onboarding", code="branch-onboarding")
    role.permissions.add(Permission.objects.get(content_type__app_label="organizations", codename="add_agentprofile"))
    agent_membership.roles.add(role)
    AgentProfile.objects.create(
        organization=organization,
        membership=agent_membership,
        profile_type=AgentProfileType.AGENT,
        status=AgentProfileStatus.ACTIVE,
        branch=assigned_branch,
        legal_name="Branch Agent",
    )
    OrganizationSetting.objects.create(
        organization=organization,
        key="allow_agent_dsa_registration",
        value=True,
    )
    client.force_login(agent_user)

    response = client.post(reverse("agent-dsa-create"), {
        "first_name": "Wrong",
        "last_name": "Branch",
        "username": "wrong-branch-dsa",
        "email": "wrong-branch-dsa@example.com",
        "branch": unassigned_branch.id,
    })

    assert response.status_code == 200
    assert b"Select a valid choice" in response.content
    assert not User.objects.filter(username="wrong-branch-dsa").exists()
