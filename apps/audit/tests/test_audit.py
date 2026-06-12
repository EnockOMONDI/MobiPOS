import pytest
from django.core.exceptions import ValidationError

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
