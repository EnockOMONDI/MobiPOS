import pytest
from django.core.exceptions import ValidationError
from django.test import RequestFactory, override_settings

from apps.audit.services import record_audit_event
from apps.audit.models import AuditEvent
from apps.accounts.models import User


@pytest.mark.django_db
def test_audit_event_is_immutable():
    event = record_audit_event(action="test.event", message="Created once")
    event.message = "Changed"

    with pytest.raises(ValidationError):
        event.save()

    with pytest.raises(ValidationError):
        event.delete()


@pytest.mark.django_db
def test_successful_login_is_audited(client):
    User.objects.create_user(
        username="owner", email="owner@example.com", password="password"
    )

    assert client.login(username="owner", password="password")
    assert AuditEvent.objects.filter(action="auth.login").exists()


@pytest.mark.django_db
@override_settings(TRUST_PROXY_IP_HEADERS=False)
def test_audit_ip_ignores_spoofed_forwarded_header_by_default():
    request = RequestFactory().get(
        "/",
        HTTP_X_FORWARDED_FOR="203.0.113.10",
        REMOTE_ADDR="127.0.0.1",
    )

    event = record_audit_event(action="test.ip", request=request)

    assert event.ip_address == "127.0.0.1"


@pytest.mark.django_db
@override_settings(TRUST_PROXY_IP_HEADERS=True)
def test_audit_ip_can_use_forwarded_header_when_configured():
    request = RequestFactory().get(
        "/",
        HTTP_X_FORWARDED_FOR="203.0.113.10",
        REMOTE_ADDR="127.0.0.1",
    )

    event = record_audit_event(action="test.ip", request=request)

    assert event.ip_address == "203.0.113.10"
