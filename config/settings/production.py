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
if not os.environ.get("REDIS_URL"):
    raise ImproperlyConfigured("Production requires REDIS_URL for background work.")
INTEGRATION_MODE = os.environ.get("INTEGRATION_MODE", "disabled")
if INTEGRATION_MODE == "sandbox":
    raise ImproperlyConfigured("Sandbox integration adapters cannot run in production.")
PRIVILEGED_OTP_REQUIRED = True

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
