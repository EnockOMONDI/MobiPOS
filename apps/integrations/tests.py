import pytest
from django.test import override_settings
from django.utils import timezone
from unittest.mock import patch

from apps.integrations.models import IntegrationEvent, IntegrationStatus
from apps.integrations.services import queue_integration_event
from apps.integrations.tasks import process_due_integration_events, process_integration_event
from apps.organizations.models import Organization


@pytest.mark.django_db
def test_integration_outbox_is_idempotent():
    org = Organization.objects.create(name="Org", slug="integration-org")
    first = queue_integration_event(organization=org, provider="etims", event_type="invoice", idempotency_key="same-key", payload={"number": "1"})
    second = queue_integration_event(organization=org, provider="etims", event_type="invoice", idempotency_key="same-key", payload={"number": "1"})

    assert first == second
    assert IntegrationEvent.objects.count() == 1


@pytest.mark.django_db
def test_due_integration_events_are_dispatched():
    org = Organization.objects.create(name="Retry Org", slug="retry-org")
    event = IntegrationEvent.objects.create(
        organization=org, provider="etims", event_type="invoice",
        idempotency_key="retry-key", payload={}, status=IntegrationStatus.FAILED,
        next_retry_at=timezone.now(),
    )
    with patch("apps.integrations.tasks.process_integration_event.delay") as delay:
        assert process_due_integration_events() == 1
        delay.assert_called_once_with(str(event.id))


@pytest.mark.django_db
def test_unknown_provider_event_is_marked_failed():
    org = Organization.objects.create(name="Unknown Org", slug="unknown-org")
    event = IntegrationEvent.objects.create(
        organization=org, provider="unknown", event_type="invoice",
        idempotency_key="unknown-key", payload={},
    )

    process_integration_event(str(event.id))
    event.refresh_from_db()

    assert event.status == IntegrationStatus.FAILED
    assert event.attempts == 1


@pytest.mark.django_db
@override_settings(INTEGRATION_MODE="disabled")
def test_placeholder_adapter_cannot_succeed_when_integrations_are_disabled():
    org = Organization.objects.create(name="Disabled Org", slug="disabled-org")
    event = IntegrationEvent.objects.create(
        organization=org, provider="etims", event_type="invoice",
        idempotency_key="disabled-key", payload={},
    )

    process_integration_event(str(event.id))
    event.refresh_from_db()

    assert event.status == IntegrationStatus.FAILED
    assert "not activated" in event.error_message

# Create your tests here.
