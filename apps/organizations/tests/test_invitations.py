import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.organizations.models import Invitation, InvitationStatus, Membership, MembershipStatus, Organization


@pytest.mark.django_db
def test_invitee_accepts_invitation_and_sets_own_password(client):
    owner = User.objects.create_user(username="owner-invite", email="owner-invite@example.com")
    invitee = User.objects.create_user(username="invitee", email="invitee@example.com", is_active=False)
    invitee.set_unusable_password()
    invitee.save(update_fields=["password"])
    organization = Organization.objects.create(name="Invite Org", slug="invite-org", status="active")
    Membership.objects.create(organization=organization, user=owner, status=MembershipStatus.ACTIVE, is_owner=True)
    membership = Membership.objects.create(organization=organization, user=invitee, status=MembershipStatus.INVITED)
    from django.utils import timezone
    from datetime import timedelta
    invitation = Invitation.objects.create(
        organization=organization, membership=membership, invited_by=owner,
        expires_at=timezone.now() + timedelta(days=1),
    )

    response = client.post(reverse("invitation-accept", args=[invitation.token]), {
        "password": "StrongPass123!",
        "password_confirm": "StrongPass123!",
        "accept_terms": "on",
    })

    invitee.refresh_from_db()
    membership.refresh_from_db()
    invitation.refresh_from_db()
    assert response.status_code == 302
    assert invitee.is_active and invitee.check_password("StrongPass123!")
    assert membership.status == MembershipStatus.ACTIVE
    assert invitation.status == InvitationStatus.ACCEPTED
