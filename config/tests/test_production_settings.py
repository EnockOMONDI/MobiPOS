import os
import subprocess
import sys


def _production_environment(**overrides):
    environment = os.environ.copy()
    environment.update({
        "DJANGO_SETTINGS_MODULE": "config.settings.production",
        "DATABASE_URL": "postgresql://mobipos:password@127.0.0.1:5432/mobipos",
        "SECRET_KEY": "production-settings-test-secret-key",
        "ALLOWED_HOSTS": "mobipos.example.com",
        "INTEGRATION_MODE": "disabled",
        "BACKGROUND_WORKERS_ENABLED": "false",
        "MEDIA_STORAGE_REQUIRED": "true",
        "MEDIA_STORAGE_BACKEND": "uploadcare",
        "UPLOADCARE_PUBLIC_KEY": "public-key",
        "UPLOADCARE_SECRET_KEY": "secret-key",
        "UPLOADCARE_SIGNED_UPLOADS": "true",
        "UPLOADCARE_SIGNED_DELIVERY": "true",
        "UPLOADCARE_SIGNING_SECRET": "0" * 64,
        "UPLOADCARE_CDN_BASE_URL": "https://mobipos.s.ucarecd.net",
        "SENTRY_REQUIRED": "true",
        "SENTRY_DSN": "https://public@example.ingest.sentry.io/1",
        "BACKUP_ENABLED": "false",
    })
    environment.update(overrides)
    return environment


def _load_production_settings(**overrides):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from django.conf import settings; print(settings.MEDIA_STORAGE_BACKEND)",
        ],
        capture_output=True,
        text=True,
        env=_production_environment(**overrides),
        check=False,
    )


def _read_production_setting(expression, **overrides):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            f"from django.conf import settings; print({expression})",
        ],
        capture_output=True,
        text=True,
        env=_production_environment(**overrides),
        check=False,
    )


def test_production_settings_accept_private_persistent_media_and_sentry():
    result = _load_production_settings()

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "uploadcare"


def test_production_settings_reject_local_media():
    result = _load_production_settings(MEDIA_STORAGE_BACKEND="local")

    assert result.returncode != 0
    assert "Production private media requires MEDIA_STORAGE_BACKEND=uploadcare" in result.stderr


def test_production_settings_reject_unsigned_media_delivery():
    result = _load_production_settings(UPLOADCARE_SIGNED_DELIVERY="false")

    assert result.returncode != 0
    assert "Production private media requires signed Uploadcare delivery" in result.stderr


def test_production_settings_reject_missing_sentry_dsn():
    result = _load_production_settings(SENTRY_DSN="")

    assert result.returncode != 0
    assert "Production error monitoring requires SENTRY_DSN" in result.stderr


def test_production_settings_reject_legacy_public_uploadcare_domain():
    result = _load_production_settings(UPLOADCARE_CDN_BASE_URL="https://ucarecdn.com")

    assert result.returncode != 0
    assert "Private media cannot use the legacy public ucarecdn.com domain" in result.stderr


def test_production_settings_enforce_csp_by_default():
    result = _read_production_setting(
        "settings.SECURE_CSP['object-src'][0]",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "'none'"


def test_production_settings_can_attach_csp_reporting_endpoint():
    result = _read_production_setting(
        "settings.SECURE_CSP['report-uri'][0]",
        CSP_REPORT_URI="https://reports.example.com/csp",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "https://reports.example.com/csp"
