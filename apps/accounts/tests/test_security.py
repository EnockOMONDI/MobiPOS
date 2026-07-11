import pytest
from django.contrib.sessions.models import Session
from django.urls import reverse

from apps.accounts.models import RecoveryCode, User, UserSession
from apps.accounts.services import consume_recovery_code, generate_recovery_codes


@pytest.mark.django_db
def test_recovery_codes_are_one_time_and_hashed():
    user = User.objects.create_user(username="secure", email="secure@example.com")

    codes = generate_recovery_codes(user=user, count=2)

    stored = RecoveryCode.objects.filter(user=user)
    assert stored.count() == 2
    assert not stored.filter(code_hash__in=codes).exists()
    assert consume_recovery_code(user=user, code=codes[0])
    assert not consume_recovery_code(user=user, code=codes[0])


@pytest.mark.django_db
def test_user_can_revoke_another_session(client):
    user = User.objects.create_user(username="sessions", email="sessions@example.com", password="StrongPass123!")
    other = Session.objects.create(session_key="other-session", session_data="", expire_date="2099-01-01T00:00:00Z")
    tracked = UserSession.objects.create(user=user, session_key=other.session_key)
    client.force_login(user)

    response = client.post(reverse("session-revoke", args=[tracked.id]))

    assert response.status_code == 302
    assert not Session.objects.filter(session_key=other.session_key).exists()
    assert not UserSession.objects.filter(pk=tracked.id).exists()
