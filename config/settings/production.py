import os

from django.core.exceptions import ImproperlyConfigured
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
MEDIA_STORAGE_REQUIRED = os.environ.get("MEDIA_STORAGE_REQUIRED", "false").lower() == "true"
if MEDIA_STORAGE_REQUIRED and MEDIA_STORAGE_BACKEND == "local":  # noqa: F405
    raise ImproperlyConfigured("Production persistent media storage requires MEDIA_STORAGE_BACKEND=uploadcare.")
if MEDIA_STORAGE_BACKEND == "uploadcare" and not UPLOADCARE_PUBLIC_KEY:  # noqa: F405
    raise ImproperlyConfigured("Uploadcare media storage requires UPLOADCARE_PUBLIC_KEY.")

DEBUG = False
SECURE_SSL_REDIRECT = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

if os.environ.get("SENTRY_DSN"):
    sentry_sdk.init(
        dsn=os.environ["SENTRY_DSN"],
        environment=os.environ.get("RENDER_SERVICE_NAME", "production"),
        traces_sample_rate=0.1,
        send_default_pii=False,
    )
