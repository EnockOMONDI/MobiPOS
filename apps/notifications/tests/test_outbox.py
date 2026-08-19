import pytest
from django.core.mail import EmailMultiAlternatives
from django.test import override_settings

from apps.notifications.models import EmailDeliveryStatus, EmailOutbox
from apps.notifications.tasks import deliver_email, process_due_emails
from apps.organizations.models import Organization


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="apps.notifications.backends.OutboxEmailBackend")
def test_outbox_backend_persists_html_email_and_dispatches_after_commit(monkeypatch, django_capture_on_commit_callbacks):
    organization = Organization.objects.create(name="Outbox Retail", slug="outbox-retail")
    dispatched = []
    monkeypatch.setattr("apps.notifications.tasks.deliver_email.delay", lambda delivery_id: dispatched.append(delivery_id))
    message = EmailMultiAlternatives(
        subject="Account ready",
        body="Set up your account",
        from_email="MobiPOS <no-reply@example.com>",
        to=["staff@example.com"],
        headers={"X-MobiPOS-Organization-ID": str(organization.id)},
    )
    message.attach_alternative("<strong>Set up your account</strong>", "text/html")

    with django_capture_on_commit_callbacks(execute=True):
        assert message.send() == 1

    delivery = EmailOutbox.objects.get()
    assert delivery.organization == organization
    assert delivery.recipient == "staff@example.com"
    assert delivery.html_body.startswith("<strong>")
    assert dispatched == [str(delivery.id)]


@pytest.mark.django_db
@override_settings(EMAIL_BACKEND="apps.notifications.backends.OutboxEmailBackend")
def test_outbox_keeps_email_when_broker_dispatch_fails(monkeypatch, django_capture_on_commit_callbacks):
    def unavailable_broker(_delivery_id):
        raise ConnectionError("broker unavailable")

    monkeypatch.setattr("apps.notifications.tasks.deliver_email.delay", unavailable_broker)
    message = EmailMultiAlternatives(
        subject="Account ready",
        body="Set up your account",
        from_email="MobiPOS <no-reply@example.com>",
        to=["staff@example.com"],
    )

    with django_capture_on_commit_callbacks(execute=True):
        assert message.send() == 1

    delivery = EmailOutbox.objects.get()
    assert delivery.status == EmailDeliveryStatus.PENDING


@pytest.mark.django_db
@override_settings(EMAIL_MAX_ATTEMPTS=5)
def test_email_delivery_records_provider_success(monkeypatch):
    delivery = EmailOutbox.objects.create(
        recipient="owner@example.com",
        subject="Summary",
        text_body="Daily summary",
        from_email="MobiPOS <no-reply@example.com>",
    )
    monkeypatch.setattr("apps.notifications.tasks.send_mailersend_email", lambda item: "provider-message-1")

    assert deliver_email(str(delivery.id)) == EmailDeliveryStatus.SENT

    delivery.refresh_from_db()
    assert delivery.status == EmailDeliveryStatus.SENT
    assert delivery.provider_message_id == "provider-message-1"
    assert delivery.attempts == 1
    assert delivery.sent_at is not None


@pytest.mark.django_db
def test_active_email_claim_is_idempotent(monkeypatch):
    delivery = EmailOutbox.objects.create(
        recipient="owner@example.com",
        subject="Summary",
        text_body="Daily summary",
        from_email="MobiPOS <no-reply@example.com>",
        status=EmailDeliveryStatus.PROCESSING,
    )
    provider_calls = []
    monkeypatch.setattr(
        "apps.notifications.tasks.send_mailersend_email",
        lambda item: provider_calls.append(item.id),
    )

    assert deliver_email(str(delivery.id)) == EmailDeliveryStatus.PROCESSING
    assert provider_calls == []


@pytest.mark.django_db
def test_stale_processing_email_is_requeued(monkeypatch):
    from django.utils import timezone

    delivery = EmailOutbox.objects.create(
        recipient="owner@example.com",
        subject="Summary",
        text_body="Daily summary",
        from_email="MobiPOS <no-reply@example.com>",
        status=EmailDeliveryStatus.PROCESSING,
    )
    EmailOutbox.objects.filter(pk=delivery.pk).update(updated_at=timezone.now() - timezone.timedelta(minutes=20))
    dispatched = []
    monkeypatch.setattr("apps.notifications.tasks.deliver_email.delay", lambda delivery_id: dispatched.append(delivery_id))

    assert process_due_emails() == 1
    assert dispatched == [str(delivery.id)]
