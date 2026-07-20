import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.notifications.models import Notification
from apps.organizations.models import Membership, MembershipStatus, Organization


@pytest.mark.django_db
def test_user_can_mark_own_notification_read(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="brian")
    notification = Notification.objects.get(recipient=user)
    client.force_login(user)

    response = client.post(reverse("notification-read", args=[notification.id]))

    notification.refresh_from_db()
    assert response.status_code == 302
    assert notification.read_at is not None


@pytest.mark.django_db
def test_user_cannot_see_or_mark_another_users_notification(client):
    organization = Organization.objects.create(name="Notify Org", slug="notify-org", status="active")
    owner = User.objects.create_user(username="notify-owner", email="notify-owner@example.com")
    other = User.objects.create_user(username="notify-other", email="notify-other@example.com")
    Membership.objects.create(
        organization=organization,
        user=owner,
        status=MembershipStatus.ACTIVE,
        is_owner=True,
    )
    Membership.objects.create(
        organization=organization,
        user=other,
        status=MembershipStatus.ACTIVE,
    )
    own_notification = Notification.objects.create(
        organization=organization,
        recipient=owner,
        title="Own alert",
        message="Visible only to owner",
    )
    other_notification = Notification.objects.create(
        organization=organization,
        recipient=other,
        title="Other alert",
        message="Should stay hidden",
    )
    client.force_login(owner)

    list_response = client.get(reverse("notification-list"))
    mark_other = client.post(reverse("notification-read", args=[other_notification.id]))
    other_notification.refresh_from_db()

    assert list_response.status_code == 200
    assert b"Own alert" in list_response.content
    assert b"Other alert" not in list_response.content
    assert mark_other.status_code == 404
    assert other_notification.read_at is None
    assert own_notification.read_at is None
