from celery import shared_task
from datetime import timedelta
from django.db import transaction
from django.utils import timezone

from .adapters import ADAPTERS
from .models import IntegrationEvent, IntegrationStatus

MAX_ATTEMPTS = 8


@shared_task(bind=True, autoretry_for=(), max_retries=0)
@transaction.atomic
def process_integration_event(self, event_id):
    event = IntegrationEvent.objects.select_for_update().get(pk=event_id)
    if event.status == IntegrationStatus.SUCCEEDED:
        return event.response
    if event.status == IntegrationStatus.DEAD:
        return {}
    event.status = IntegrationStatus.PROCESSING
    event.attempts += 1
    event.save(update_fields=["status", "attempts", "updated_at"])
    try:
        event.response = ADAPTERS[event.provider].submit(event)
        event.status = IntegrationStatus.SUCCEEDED
        event.error_message = ""
        event.next_retry_at = None
    except Exception as exc:
        event.status = IntegrationStatus.DEAD if event.attempts >= MAX_ATTEMPTS else IntegrationStatus.FAILED
        event.error_message = str(exc)
        event.next_retry_at = None if event.status == IntegrationStatus.DEAD else timezone.now() + timedelta(minutes=5)
    event.save(update_fields=["attempts", "response", "status", "error_message", "next_retry_at", "updated_at"])
    return event.response


@shared_task
def process_due_integration_events():
    event_ids = list(
        IntegrationEvent.objects.filter(
            status__in=[IntegrationStatus.PENDING, IntegrationStatus.FAILED],
        )
        .filter(next_retry_at__isnull=True)
        .values_list("id", flat=True)[:100]
    )
    due_failed_ids = list(
        IntegrationEvent.objects.filter(
            status=IntegrationStatus.FAILED,
            next_retry_at__lte=timezone.now(),
        ).values_list("id", flat=True)[:100]
    )
    for event_id in dict.fromkeys([*event_ids, *due_failed_ids]):
        process_integration_event.delay(str(event_id))
    return len(set([*event_ids, *due_failed_ids]))
