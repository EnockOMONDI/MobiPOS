import os
from urllib.parse import urlparse

from django.core.exceptions import ImproperlyConfigured
from django.utils.csp import CSP
import sentry_sdk

from .base import *  # noqa: F403

database_url = os.environ.get("DATABASE_URL", "")
if not database_url or database_url.startswith("sqlite"):
    raise ImproperlyConfigured("Production requires a PostgreSQL DATABASE_URL.")
if SECRET_KEY == "unsafe-local-development-key":  # noqa: F405
    raise ImproperlyConfigured("Production requires a strong SECRET_KEY.")
if not ALLOWED_HOSTS:  # noqa: F405
    raise ImproperlyConfigured("Production requires ALLOWED_HOSTS.")
BACKGROUND_WORKERS_ENABLED = os.environ.get("BACKGROUND_WORKERS_ENABLED", "false").lower() == "true"
if BACKGROUND_WORKERS_ENABLED and not os.environ.get("REDIS_URL"):
    raise ImproperlyConfigured("Production background workers require REDIS_URL.")
if not BACKGROUND_WORKERS_ENABLED and not os.environ.get("REDIS_URL"):
    CELERY_TASK_ALWAYS_EAGER = True  # noqa: F405
    CELERY_BROKER_URL = "memory://"  # noqa: F405
    CELERY_RESULT_BACKEND = "cache+memory://"  # noqa: F405
INTEGRATION_MODE = os.environ.get("INTEGRATION_MODE", "disabled")
if INTEGRATION_MODE == "sandbox":
    raise ImproperlyConfigured("Sandbox integration adapters cannot run in production.")
PRIVILEGED_OTP_REQUIRED = os.environ.get("PRIVILEGED_OTP_REQUIRED", "true").lower() == "true"
POS_AUTO_OPEN_SESSION = os.environ.get("POS_AUTO_OPEN_SESSION", "false").lower() == "true"
EMAIL_DELIVERY_REQUIRED = os.environ.get("EMAIL_DELIVERY_REQUIRED", "false").lower() == "true"
if EMAIL_DELIVERY_REQUIRED and EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend":  # noqa: F405
    raise ImproperlyConfigured("Production email delivery requires a real EMAIL_BACKEND.")
if EMAIL_DELIVERY_REQUIRED and EMAIL_BACKEND == "django.core.mail.backends.smtp.EmailBackend" and not EMAIL_HOST:  # noqa: F405
    raise ImproperlyConfigured("SMTP email delivery requires EMAIL_HOST.")
if EMAIL_DELIVERY_REQUIRED and EMAIL_BACKEND == "apps.notifications.backends.OutboxEmailBackend" and not MAILERSEND_API_TOKEN:  # noqa: F405
    raise ImproperlyConfigured("MailerSend API delivery requires MAILERSEND_API_TOKEN.")
if EMAIL_BACKEND == "apps.notifications.backends.OutboxEmailBackend" and not BACKGROUND_WORKERS_ENABLED:  # noqa: F405
    raise ImproperlyConfigured("Queued MailerSend delivery requires BACKGROUND_WORKERS_ENABLED=true and a Render worker.")
MEDIA_STORAGE_REQUIRED = os.environ.get("MEDIA_STORAGE_REQUIRED", "true").lower() == "true"
if not MEDIA_STORAGE_REQUIRED:
    raise ImproperlyConfigured("Production private persistent media storage cannot be disabled.")
if MEDIA_STORAGE_BACKEND != "uploadcare":  # noqa: F405
    raise ImproperlyConfigured("Production private media requires MEDIA_STORAGE_BACKEND=uploadcare.")
if not UPLOADCARE_PUBLIC_KEY or not UPLOADCARE_SECRET_KEY:  # noqa: F405
    raise ImproperlyConfigured("Uploadcare media storage requires public and secret keys.")
if not UPLOADCARE_SIGNED_UPLOADS:  # noqa: F405
    raise ImproperlyConfigured("Production Uploadcare storage requires UPLOADCARE_SIGNED_UPLOADS=true.")
if not UPLOADCARE_SIGNED_DELIVERY or not UPLOADCARE_SIGNING_SECRET:  # noqa: F405
    raise ImproperlyConfigured(
        "Production private media requires signed Uploadcare delivery and UPLOADCARE_SIGNING_SECRET."
    )
uploadcare_delivery_url = urlparse(UPLOADCARE_CDN_BASE_URL)  # noqa: F405
if uploadcare_delivery_url.scheme != "https" or not uploadcare_delivery_url.netloc:
    raise ImproperlyConfigured("UPLOADCARE_CDN_BASE_URL must be an explicit HTTPS secure delivery domain.")
if uploadcare_delivery_url.netloc.lower() == "ucarecdn.com":
    raise ImproperlyConfigured(
        "Private media cannot use the legacy public ucarecdn.com domain. Configure the project's secure Uploadcare domain."
    )
if BACKUP_ENABLED:  # noqa: F405
    if not BACKUP_SOURCE_DATABASE_URL or not BACKUP_ENCRYPTION_KEY:  # noqa: F405
        raise ImproperlyConfigured("Backups require BACKUP_SOURCE_DATABASE_URL and BACKUP_ENCRYPTION_KEY.")
    if BACKUP_STORAGE_BACKEND != "supabase":  # noqa: F405
        raise ImproperlyConfigured("Production backups require separately stored Supabase object storage.")
    if not all([  # noqa: F405
        BACKUP_SUPABASE_URL, BACKUP_SUPABASE_SERVICE_KEY, BACKUP_SUPABASE_BUCKET  # noqa: F405
    ]):
        raise ImproperlyConfigured("Supabase backup storage requires URL, service key, and bucket.")

DEBUG = False
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# Set true temporarily when collecting violations during a controlled rollout.
# Production defaults to enforcement so CSP is not silently report-only.
CSP_REPORT_ONLY_ENABLED = os.environ.get("CSP_REPORT_ONLY_ENABLED", "false").lower() == "true"
CSP_REPORT_URI = os.environ.get("CSP_REPORT_URI", "").strip()
_CSP_POLICY = {
        "default-src": [CSP.SELF],
        "base-uri": [CSP.SELF],
        "connect-src": [CSP.SELF, "https://*.usertour.io", "wss://*.usertour.io"],
        "font-src": [CSP.SELF, "data:"],
        "form-action": [CSP.SELF],
        "frame-ancestors": [CSP.SELF],
        "frame-src": [CSP.SELF],
        "img-src": [CSP.SELF, "data:", "blob:", "https:"],
        "media-src": [CSP.SELF, "blob:", "https:"],
        "object-src": [CSP.NONE],
        "script-src": [
            CSP.SELF,
            CSP.UNSAFE_INLINE,
            "https://cdn.jsdelivr.net",
            "https://js.usertour.io",
        ],
        "style-src": [CSP.SELF, CSP.UNSAFE_INLINE],
        "worker-src": [CSP.SELF, "blob:"],
}
if CSP_REPORT_ONLY_ENABLED:
    SECURE_CSP_REPORT_ONLY = _CSP_POLICY
else:
    SECURE_CSP = _CSP_POLICY
if CSP_REPORT_URI:
    (SECURE_CSP_REPORT_ONLY if CSP_REPORT_ONLY_ENABLED else SECURE_CSP)["report-uri"] = [CSP_REPORT_URI]

SENTRY_REQUIRED = os.environ.get("SENTRY_REQUIRED", "true").lower() == "true"
if SENTRY_REQUIRED and not os.environ.get("SENTRY_DSN"):
    raise ImproperlyConfigured("Production error monitoring requires SENTRY_DSN.")
if os.environ.get("SENTRY_DSN"):
    sentry_sdk.init(
        dsn=os.environ["SENTRY_DSN"],
        environment=os.environ.get("RENDER_SERVICE_NAME", "production"),
        traces_sample_rate=0.1,
        send_default_pii=False,
    )
