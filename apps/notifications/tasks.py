from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.organizations.models import Organization, OrganizationStatus

from .emailing import send_daily_owner_activity_summary
from .models import EmailDeliveryStatus, EmailOutbox
from .provider import EmailProviderError, send_mailersend_email


@shared_task
def deliver_email(delivery_id):
    with transaction.atomic():
        delivery = EmailOutbox.objects.select_for_update().get(pk=delivery_id)
        if delivery.status in {EmailDeliveryStatus.SENT, EmailDeliveryStatus.DEAD}:
            return delivery.status
        if delivery.status == EmailDeliveryStatus.PROCESSING:
            # A second worker must not send the same message while the first
            # worker still owns the delivery claim. Only stale claims retry.
            if delivery.updated_at > timezone.now() - timedelta(minutes=15):
                return delivery.status
            delivery.status = EmailDeliveryStatus.FAILED
        delivery.status = EmailDeliveryStatus.PROCESSING
        delivery.attempts += 1
        delivery.save(update_fields=["status", "attempts", "updated_at"])
    try:
        provider_message_id = send_mailersend_email(delivery)
    except EmailProviderError as error:
        delivery.status = EmailDeliveryStatus.DEAD if delivery.attempts >= settings.EMAIL_MAX_ATTEMPTS else EmailDeliveryStatus.FAILED
        delivery.last_error = str(error)[:1000]
        delivery.next_attempt_at = None if delivery.status == EmailDeliveryStatus.DEAD else timezone.now() + timedelta(minutes=5 * delivery.attempts)
        delivery.save(update_fields=["status", "last_error", "next_attempt_at", "updated_at"])
        return delivery.status
    delivery.status = EmailDeliveryStatus.SENT
    delivery.provider_message_id = provider_message_id
    delivery.sent_at = timezone.now()
    delivery.next_attempt_at = None
    delivery.last_error = ""
    delivery.save(update_fields=["status", "provider_message_id", "sent_at", "next_attempt_at", "last_error", "updated_at"])
    return delivery.status


@shared_task
def process_due_emails():
    now = timezone.now()
    ids = list(
        EmailOutbox.objects.filter(status__in=[EmailDeliveryStatus.PENDING, EmailDeliveryStatus.FAILED])
        .filter(next_attempt_at__isnull=True)
        .values_list("id", flat=True)[:100]
    )
    ids += list(
        EmailOutbox.objects.filter(status=EmailDeliveryStatus.FAILED, next_attempt_at__lte=now)
        .values_list("id", flat=True)[:100]
    )
    ids += list(
        EmailOutbox.objects.filter(
            status=EmailDeliveryStatus.PROCESSING,
            updated_at__lte=now - timedelta(minutes=15),
        ).values_list("id", flat=True)[:100]
    )
    unique_ids = list(dict.fromkeys(ids))
    for delivery_id in unique_ids:
        deliver_email.delay(str(delivery_id))
    return len(unique_ids)


@shared_task
def redact_old_email_content():
    cutoff = timezone.now() - timedelta(days=settings.EMAIL_CONTENT_RETENTION_DAYS)
    return EmailOutbox.objects.filter(
        created_at__lt=cutoff,
        content_redacted_at__isnull=True,
        status__in=[EmailDeliveryStatus.SENT, EmailDeliveryStatus.DEAD],
    ).update(text_body="", html_body="", content_redacted_at=timezone.now())


@shared_task
def send_daily_owner_summaries():
    count = 0
    for organization in Organization.objects.filter(status=OrganizationStatus.ACTIVE).iterator():
        result = send_daily_owner_activity_summary(organization=organization)
        count += result.sent_count
    return count
