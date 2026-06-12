import pytest
from django.core.management import call_command
from django.urls import reverse

from apps.accounts.models import User
from apps.notifications.models import Notification


@pytest.mark.django_db
def test_user_can_mark_own_notification_read(client):
    call_command("seed_demo_data")
    user = User.objects.get(username="alice")
    notification = Notification.objects.get(recipient=user)
    client.force_login(user)

    response = client.post(reverse("notification-read", args=[notification.id]))

    notification.refresh_from_db()
    assert response.status_code == 302
    assert notification.read_at is not None
