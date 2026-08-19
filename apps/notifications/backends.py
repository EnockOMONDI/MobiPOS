from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend
from django.db import transaction

from .models import EmailOutbox


class OutboxEmailBackend(BaseEmailBackend):
    """Persist email before delivery so web requests never depend on the provider."""

    def send_messages(self, email_messages):
        queued = 0
        for message in email_messages:
            html_body = ""
            for alternative in getattr(message, "alternatives", []):
                if hasattr(alternative, "content"):
                    content = alternative.content
                    mimetype = alternative.mimetype
                else:
                    content, mimetype = alternative
                if mimetype == "text/html":
                    html_body = content
                    break
            for recipient in message.to or []:
                delivery = EmailOutbox.objects.create(
                    organization_id=(message.extra_headers or {}).get("X-MobiPOS-Organization-ID") or None,
                    recipient=recipient,
                    subject=message.subject,
                    text_body=message.body,
                    html_body=html_body,
                    from_email=message.from_email or settings.DEFAULT_FROM_EMAIL,
                )
                from .tasks import deliver_email

                transaction.on_commit(
                    lambda delivery_id=str(delivery.id): deliver_email.delay(delivery_id),
                    robust=True,
                )
                queued += 1
        return queued
