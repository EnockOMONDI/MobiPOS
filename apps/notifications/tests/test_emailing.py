import pytest
from django.core import mail
from django.core.mail import EmailMultiAlternatives
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from apps.accounts.models import User
from apps.audit.services import record_audit_event
from apps.organizations.models import Branch, Membership, Organization, Plan, Role


EMAIL_SETTINGS = {
    "EMAIL_BACKEND": "django.core.mail.backends.locmem.EmailBackend",
    "DEFAULT_FROM_EMAIL": "MobiPOS <no-reply@example.com>",
    "APP_BASE_URL": "https://kipekeestudio.co.ke",
}


@pytest.mark.django_db
@override_settings(**EMAIL_SETTINGS)
def test_owner_registration_sends_branded_welcome_email(client, django_capture_on_commit_callbacks):
    plan = Plan.objects.create(name="Starter", code="starter", monthly_price=1000)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("register-organization"),
            {
                "organization_name": "Email Retailer",
                "first_name": "Email",
                "last_name": "Owner",
                "email": "email.owner@example.com",
                "password": "SecurePass123!",
                "plan": plan.id,
            },
        )

    assert response.status_code == 302
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["email.owner@example.com"]
    assert "Welcome to MobiPOS" in message.subject
    assert "Open dashboard" in message.alternatives[0][0]
    assert "Email Retailer" in message.body


@pytest.mark.django_db
@override_settings(**EMAIL_SETTINGS)
def test_owner_registration_succeeds_when_welcome_email_times_out(client, monkeypatch, django_capture_on_commit_callbacks):
    plan = Plan.objects.create(name="Starter", code="starter", monthly_price=1000)

    def raise_timeout(self, *args, **kwargs):
        raise TimeoutError("SMTP timed out")

    monkeypatch.setattr(EmailMultiAlternatives, "send", raise_timeout)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("register-organization"),
            {
                "organization_name": "Timeout Retailer",
                "first_name": "Timeout",
                "last_name": "Owner",
                "email": "timeout.owner@example.com",
                "password": "SecurePass123!",
                "plan": plan.id,
            },
        )

    assert response.status_code == 302
    assert Organization.objects.filter(slug="timeout-retailer").exists()
    assert User.objects.filter(email="timeout.owner@example.com").exists()


@pytest.mark.django_db
@override_settings(**EMAIL_SETTINGS)
def test_staff_creation_sends_account_ready_email(client, django_capture_on_commit_callbacks):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    branch = Branch.objects.get(organization=organization, code="WST")
    role = Role.objects.get(organization=organization, code="cashier")
    client.force_login(owner)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("tenant-user-create"),
            {
                "first_name": "Email",
                "last_name": "Cashier",
                "email": "email.cashier@example.com",
                "branches": [branch.id],
                "roles": [role.id],
            },
        )

    assert response.status_code == 302
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["email.cashier@example.com"]
    assert "account for Nairobi Mobile Hub is ready" in message.subject
    assert "Sign-in email" in message.body
    assert "secure, single-use link" in message.body
    assert "/accounts/setup/" in message.body
    assert "Set up my account" in message.alternatives[0][0]


@pytest.mark.django_db
@override_settings(**EMAIL_SETTINGS)
def test_staff_creation_succeeds_when_account_email_times_out(client, monkeypatch, django_capture_on_commit_callbacks):
    call_command("seed_demo_data")
    owner = User.objects.get(username="brian")
    organization = Organization.objects.get(slug="nairobi-mobile-hub")
    branch = Branch.objects.get(organization=organization, code="WST")
    role = Role.objects.get(organization=organization, code="cashier")
    client.force_login(owner)

    def raise_timeout(self, *args, **kwargs):
        raise TimeoutError("SMTP timed out")

    monkeypatch.setattr(EmailMultiAlternatives, "send", raise_timeout)

    with django_capture_on_commit_callbacks(execute=True):
        response = client.post(
            reverse("tenant-user-create"),
            {
                "first_name": "Timeout",
                "last_name": "Cashier",
                "email": "timeout.cashier@example.com",
                "branches": [branch.id],
                "roles": [role.id],
            },
        )

    assert response.status_code == 302
    membership = Membership.objects.get(organization=organization, user__email="timeout.cashier@example.com")
    assert membership.status == "active"


@pytest.mark.django_db
@override_settings(**EMAIL_SETTINGS)
def test_password_reset_sends_branded_html_email(client):
    User.objects.create_user(username="reset-user", email="reset@example.com", password="StrongPass123!")

    response = client.post(reverse("password-reset"), {"email": "reset@example.com"})

    assert response.status_code == 302
    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["reset@example.com"]
    assert "Reset your MobiPOS password" in message.subject
    assert "We received a request to reset your MobiPOS password" in message.body
    assert message.alternatives
    assert "Reset password" in message.alternatives[0][0]


@pytest.mark.django_db
@override_settings(**EMAIL_SETTINGS)
def test_daily_owner_activity_summary_command_sends_owner_email():
    organization = Organization.objects.create(name="Summary Retailer", slug="summary-retailer", status="active")
    owner = User.objects.create_user(username="summary-owner", email="summary.owner@example.com")
    Membership.objects.create(organization=organization, user=owner, status="active", is_owner=True)
    record_audit_event(action="contact.created", actor=owner, organization=organization, message="Supplier added")

    call_command("send_daily_owner_activity_summary", organization="summary-retailer")

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["summary.owner@example.com"]
    assert "Daily MobiPOS activity summary" in message.subject
    assert "Supplier added" in message.body
    assert "Open activity report" in message.alternatives[0][0]
