import pytest
from django.core.management import call_command
from django.db.models.deletion import ProtectedError
from django.urls import reverse

from apps.accounts.models import User
from apps.notifications.models import Notification
from apps.notifications.services import notify_business_event
from apps.operations.models import ApprovalRequest, ApprovalStatus
from apps.operations.services import request_approval
from apps.organizations.models import Membership, MembershipStatus, Organization


@pytest.mark.django_db(transaction=True)
def test_business_event_deduplicates_recipients_and_queues_email(monkeypatch):
    organization = Organization.objects.create(name="Event Org", slug="event-org", status="active")
    owner = User.objects.create_user(username="event-owner", email="owner@example.com")
    Membership.objects.create(
        organization=organization,
        user=owner,
        status=MembershipStatus.ACTIVE,
        is_owner=True,
    )
    deliveries = []

    def capture_delivery(**kwargs):
        deliveries.append(kwargs)

    monkeypatch.setattr("apps.notifications.services.send_branded_email", capture_delivery)

    recipient_count = notify_business_event(
        organization=organization,
        title="Transfer dispatched",
        message="TRF-001 is moving to Nairobi CBD.",
        link="/transfers/example/",
        users=(owner,),
        include_owners=True,
    )

    notification = Notification.objects.get(recipient=owner)
    assert recipient_count == 1
    assert notification.title == "Transfer dispatched"
    assert notification.link == "/transfers/example/"
    assert len(deliveries) == 1
    assert deliveries[0]["recipient_list"] == ["owner@example.com"]


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


@pytest.mark.django_db
def test_notification_center_paginates_without_truncating_history(client):
    organization = Organization.objects.create(name="History Org", slug="notify-history", status="active")
    owner = User.objects.create_user(username="history-owner", email="history-owner@example.com")
    Membership.objects.create(
        organization=organization,
        user=owner,
        status=MembershipStatus.ACTIVE,
        is_owner=True,
    )
    Notification.objects.bulk_create([
        Notification(
            organization=organization,
            recipient=owner,
            title=f"Notice {index:03d}",
            message="Operational update",
        )
        for index in range(105)
    ])
    client.force_login(owner)

    first_page = client.get(reverse("notification-list"))
    last_page = client.get(reverse("notification-list"), {"page": 6})

    assert first_page.status_code == 200
    assert first_page.context["page_obj"].paginator.count == 105
    assert len(first_page.context["notification_cards"]) == 20
    assert len(last_page.context["notification_cards"]) == 5


@pytest.mark.django_db
def test_approval_notification_is_typed_and_can_approve_once(client):
    organization = Organization.objects.create(name="Action Org", slug="notify-action", status="active")
    requester = User.objects.create_user(username="action-requester", email="requester@example.com")
    owner = User.objects.create_user(username="action-owner", email="owner@example.com")
    Membership.objects.create(organization=organization, user=requester, status=MembershipStatus.ACTIVE)
    Membership.objects.create(
        organization=organization,
        user=owner,
        status=MembershipStatus.ACTIVE,
        is_owner=True,
    )
    approval = request_approval(
        organization=organization,
        request_type="credit_sale",
        target=organization,
        requested_by=requester,
        reason="Customer needs owner-approved credit.",
    )
    notification = Notification.objects.get(recipient=owner)
    client.force_login(owner)

    response = client.post(reverse("approval-decide", args=[approval.id]), {"decision": "approved"})
    repeated = client.post(reverse("approval-decide", args=[approval.id]), {"decision": "approved"})

    approval.refresh_from_db()
    notification.refresh_from_db()
    assert response.status_code == 302
    assert repeated.status_code == 302
    assert approval.status == ApprovalStatus.APPROVED
    assert notification.kind == Notification.Kind.APPROVAL_REQUEST
    assert notification.approval_id == approval.id
    assert notification.read_at is not None
    assert ApprovalRequest.objects.filter(id=approval.id).count() == 1


@pytest.mark.django_db
def test_notification_history_protects_recipient_from_deletion():
    organization = Organization.objects.create(name="Protected Org", slug="notify-protected", status="active")
    owner = User.objects.create_user(username="protected-owner", email="protected-owner@example.com")
    Membership.objects.create(
        organization=organization,
        user=owner,
        status=MembershipStatus.ACTIVE,
        is_owner=True,
    )
    Notification.objects.create(
        organization=organization,
        recipient=owner,
        title="Retained event",
        message="This accountability record must remain.",
    )

    with pytest.raises(ProtectedError):
        owner.delete()
