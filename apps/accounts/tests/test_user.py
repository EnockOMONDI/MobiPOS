import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import AccountSetupToken, User
from apps.accounts.setup_tokens import issue_account_setup_token
from apps.organizations.models import Organization


@pytest.mark.django_db
def test_user_uses_uuid_and_unique_email():
    user = User.objects.create_user(username="owner", email="owner@example.com")

    assert user.pk is not None
    assert str(user) == "owner@example.com"
    assert not user.is_demo_account


@pytest.mark.django_db
def test_login_uses_email_address(client):
    User.objects.create_user(username="internal-handle", email="person@example.com", password="StrongPass123!")

    response = client.post(reverse("login"), {"username": "person@example.com", "password": "StrongPass123!"})

    assert response.status_code == 302


@pytest.mark.django_db
def test_login_page_uses_email_language(client):
    response = client.post(reverse("login"), {"username": "missing@example.com", "password": "wrong"})

    assert response.status_code == 200
    assert b"correct email address and password" in response.content
    assert b"correct username and password" not in response.content


@pytest.mark.django_db
@override_settings(ACCOUNT_SETUP_TOKEN_DAYS=3)
def test_account_setup_token_is_single_use_and_enables_email_login(client):
    organization = Organization.objects.create(name="Setup Company", slug="setup-company")
    user = User.objects.create_user(
        username="generated-handle",
        email="staff@example.com",
        password=None,
        requires_password_setup=True,
    )
    setup_token, raw_token = issue_account_setup_token(user=user, organization=organization)

    assert setup_token.expires_at > timezone.now()
    assert setup_token.expires_at <= timezone.now() + timezone.timedelta(days=3, seconds=5)
    response = client.post(
        reverse("account-setup", args=[raw_token]),
        {
            "new_password1": "SecureStaffPass123!",
            "new_password2": "SecureStaffPass123!",
            "accept_terms": "on",
        },
    )

    user.refresh_from_db()
    setup_token.refresh_from_db()
    assert response.status_code == 302
    assert not user.requires_password_setup
    assert user.check_password("SecureStaffPass123!")
    assert setup_token.used_at is not None
    assert client.get(reverse("account-setup", args=[raw_token])).status_code == 404
    assert client.login(username="staff@example.com", password="SecureStaffPass123!")


@pytest.mark.django_db
def test_expired_account_setup_token_is_rejected(client):
    organization = Organization.objects.create(name="Expired Company", slug="expired-company")
    user = User.objects.create_user(username="expired", email="expired@example.com", password=None)
    _, raw_token = issue_account_setup_token(user=user, organization=organization)
    AccountSetupToken.objects.update(expires_at=timezone.now() - timezone.timedelta(seconds=1))

    assert client.get(reverse("account-setup", args=[raw_token])).status_code == 404
