from .models import IntegrationEvent


def queue_integration_event(*, organization, provider, event_type, idempotency_key, payload):
    event, _ = IntegrationEvent.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            "organization": organization,
            "provider": provider,
            "event_type": event_type,
            "payload": payload,
        },
    )
    return event
