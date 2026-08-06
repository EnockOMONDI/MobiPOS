import re

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


@pytest.mark.django_db
def test_mfa_setup_preserves_safe_next_url(client):
    user = User.objects.create_user(username="mfa-setup-owner", email="mfa-setup@example.com")
    client.force_login(user)

    response = client.get(f"{reverse('mfa-setup')}?next=/branches/new/")

    assert response.status_code == 200
    content = response.content.decode()
    assert 'name="next" value="/branches/new/"' in content


@pytest.mark.django_db
def test_mfa_setup_rejects_unsafe_next_url(client):
    user = User.objects.create_user(username="unsafe-mfa-setup-owner", email="unsafe-mfa-setup@example.com")
    client.force_login(user)

    response = client.get(f"{reverse('mfa-setup')}?next=https://evil.example/branches/new/")

    assert response.status_code == 200
    content = response.content.decode()
    setup_form = re.search(r"<form method=\"post\" class=\"mt-6 space-y-4\">(?P<form>.*?)</form>", content, re.S).group("form")
    assert 'name="next"' not in setup_form


@pytest.mark.django_db
def test_mfa_verify_links_unenrolled_user_to_setup_with_next(client):
    user = User.objects.create_user(username="unenrolled-owner", email="unenrolled@example.com")
    client.force_login(user)

    response = client.get(f"{reverse('mfa-verify')}?next=/branches/new/")

    assert response.status_code == 200
    content = response.content.decode()
    assert reverse("mfa-setup") in content
    assert "next=/branches/new/" in content or "next=%2Fbranches%2Fnew%2F" in content
