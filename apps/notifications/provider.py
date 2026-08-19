import json
from email.utils import parseaddr
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


class EmailProviderError(RuntimeError):
    pass


def send_mailersend_email(delivery):
    if not settings.MAILERSEND_API_TOKEN:
        raise EmailProviderError("MAILERSEND_API_TOKEN is not configured.")
    from_name, from_address = parseaddr(delivery.from_email)
    payload = {
        "from": {"email": from_address, "name": from_name or "MobiPOS"},
        "to": [{"email": delivery.recipient}],
        "subject": delivery.subject,
        "text": delivery.text_body,
    }
    if delivery.html_body:
        payload["html"] = delivery.html_body
    request = Request(
        settings.MAILERSEND_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {settings.MAILERSEND_API_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.EMAIL_TIMEOUT) as response:
            if response.status != 202:
                raise EmailProviderError(f"MailerSend returned HTTP {response.status}.")
            return response.headers.get("x-message-id", "")
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:500]
        raise EmailProviderError(f"MailerSend returned HTTP {error.code}: {detail}") from error
    except (URLError, TimeoutError, OSError) as error:
        raise EmailProviderError("MailerSend could not be reached.") from error
