import os
from pathlib import Path

import dj_database_url

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = os.environ.get("SECRET_KEY", "unsafe-local-development-key")
DEBUG = False
ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]

DJANGO_APPS = [
    "unfold",
    "unfold.contrib.filters",
    "unfold.contrib.forms",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "django_ckeditor_5",
    "django_otp",
    "django_otp.plugins.otp_totp",
    "axes",
]

LOCAL_APPS = [
    "apps.accounts",
    "apps.organizations",
    "apps.catalog",
    "apps.contacts",
    "apps.inventory",
    "apps.purchasing",
    "apps.transfers",
    "apps.sales",
    "apps.pos",
    "apps.payments",
    "apps.commissions",
    "apps.expenses",
    "apps.operations",
    "apps.repairs",
    "apps.reports",
    "apps.notifications",
    "apps.integrations",
    "apps.audit",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "config.middleware.RequestIDMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django_otp.middleware.OTPMiddleware",
    "axes.middleware.AxesMiddleware",
    "apps.accounts.middleware.UserSessionTrackingMiddleware",
    "apps.organizations.middleware.OrganizationContextMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.accounts.context_processors.usertour_context",
                "apps.organizations.context_processors.organization_context",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=60,
    )
}
DATABASES["default"]["CONN_HEALTH_CHECKS"] = True

AUTH_USER_MODEL = "accounts.User"
AUTHENTICATION_BACKENDS = [
    "axes.backends.AxesStandaloneBackend",
    "django.contrib.auth.backends.ModelBackend",
]
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Africa/Nairobi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}
MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"
MEDIA_STORAGE_BACKEND = os.environ.get("MEDIA_STORAGE_BACKEND", "local")
UPLOADCARE_PUBLIC_KEY = os.environ.get("UPLOADCARE_PUBLIC_KEY", "")
UPLOADCARE_SECRET_KEY = os.environ.get("UPLOADCARE_SECRET_KEY", "")
UPLOADCARE_STORE = os.environ.get("UPLOADCARE_STORE", "1")
UPLOADCARE_SIGNED_UPLOADS = os.environ.get("UPLOADCARE_SIGNED_UPLOADS", "false").lower() == "true"
UPLOADCARE_SIGNED_DELIVERY = os.environ.get("UPLOADCARE_SIGNED_DELIVERY", "false").lower() == "true"
UPLOADCARE_SIGNING_SECRET = os.environ.get("UPLOADCARE_SIGNING_SECRET", "")
UPLOADCARE_SIGNED_URL_TTL = int(os.environ.get("UPLOADCARE_SIGNED_URL_TTL", "300"))
UPLOADCARE_CDN_BASE_URL = os.environ.get("UPLOADCARE_CDN_BASE_URL", "https://ucarecdn.com")
UPLOADCARE_UPLOAD_TIMEOUT = int(os.environ.get("UPLOADCARE_UPLOAD_TIMEOUT", "30"))
if MEDIA_STORAGE_BACKEND == "uploadcare":
    STORAGES["default"] = {"BACKEND": "config.storage.UploadcareMediaStorage"}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

AXES_FAILURE_LIMIT = 5
AXES_COOLOFF_TIME = 1
AXES_RESET_ON_SUCCESS = True
AXES_LOCKOUT_PARAMETERS = [["username", "ip_address"]]

CKEDITOR_5_CONFIGS = {
    "default": {
        "toolbar": [
            "heading",
            "|",
            "bold",
            "italic",
            "link",
            "bulletedList",
            "numberedList",
            "|",
            "undo",
            "redo",
        ]
    }
}

UNFOLD = {
    "SITE_TITLE": "MobiPOS Administration",
    "SITE_HEADER": "MobiPOS",
    "SITE_SYMBOL": "point_of_sale",
    "SHOW_HISTORY": True,
    "SHOW_VIEW_ON_SITE": False,
    "ENVIRONMENT": "apps.audit.admin.environment_callback",
}

CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = CELERY_BROKER_URL
CELERY_TASK_ALWAYS_EAGER = False
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 300
CELERY_BEAT_SCHEDULE = {
    "retry-due-integration-events": {
        "task": "apps.integrations.tasks.process_due_integration_events",
        "schedule": 60.0,
    },
    "refresh-subscription-lifecycle": {
        "task": "apps.organizations.tasks.refresh_subscriptions",
        "schedule": 60 * 60 * 24,
    },
}
INTEGRATION_MODE = os.environ.get("INTEGRATION_MODE", "sandbox")
PRIVILEGED_OTP_REQUIRED = os.environ.get("PRIVILEGED_OTP_REQUIRED", "false").lower() == "true"
POS_AUTO_OPEN_SESSION = os.environ.get("POS_AUTO_OPEN_SESSION", "true").lower() == "true"
USERTOUR_ENABLED = os.environ.get("USERTOUR_ENABLED", "false").lower() == "true"
USERTOUR_TOKEN = os.environ.get("USERTOUR_TOKEN", "")
USERTOUR_DEMO_ONLY = os.environ.get("USERTOUR_DEMO_ONLY", "true").lower() == "true"
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "MobiPOS <no-reply@mobipos.local>")
SERVER_EMAIL = os.environ.get("SERVER_EMAIL", DEFAULT_FROM_EMAIL)
EMAIL_HOST = os.environ.get("EMAIL_HOST", "")
EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "true").lower() == "true"
EMAIL_USE_SSL = os.environ.get("EMAIL_USE_SSL", "false").lower() == "true"
EMAIL_TIMEOUT = int(os.environ.get("EMAIL_TIMEOUT", "10"))

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_context": {"()": "config.logging.RequestContextFilter"},
    },
    "formatters": {
        "json": {"()": "config.logging.JSONFormatter"},
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["request_context"],
            "formatter": "json",
        },
    },
    "root": {"handlers": ["console"], "level": os.environ.get("LOG_LEVEL", "INFO")},
    "loggers": {
        "django.server": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "django.request": {"handlers": ["console"], "level": "WARNING", "propagate": False},
    },
}

SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 12
CSRF_COOKIE_HTTPONLY = True
CSRF_COOKIE_SAMESITE = "Lax"
X_FRAME_OPTIONS = "DENY"
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "same-origin"
