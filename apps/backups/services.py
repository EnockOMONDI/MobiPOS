import hashlib
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from .models import BackupRun, BackupStatus
from .storage import delete_artifact, read_artifact, store_artifact


class BackupOperationError(RuntimeError):
    pass


def _validate_configuration():
    if not settings.BACKUP_SOURCE_DATABASE_URL:
        raise ImproperlyConfigured("BACKUP_SOURCE_DATABASE_URL is not configured.")
    if not settings.BACKUP_ENCRYPTION_KEY:
        raise ImproperlyConfigured("BACKUP_ENCRYPTION_KEY is not configured.")
    return Fernet(settings.BACKUP_ENCRYPTION_KEY.encode("ascii"))


def _postgres_connection_args(database_url):
    parsed = urlparse(database_url)
    if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.path:
        raise BackupOperationError("Backup database URL must be a valid PostgreSQL URL.")
    command = [
        "--host", parsed.hostname,
        "--port", str(parsed.port or 5432),
        "--username", unquote(parsed.username or "postgres"),
        "--dbname", unquote(parsed.path.lstrip("/")),
    ]
    environment = os.environ.copy()
    if parsed.password:
        environment["PGPASSWORD"] = unquote(parsed.password)
    query = parsed.query.lower()
    if "sslmode=" in query:
        for part in parsed.query.split("&"):
            if part.startswith("sslmode="):
                environment["PGSSLMODE"] = part.split("=", maxsplit=1)[1]
                break
    return command, environment


def create_database_backup(*, started_by=None):
    cipher = _validate_configuration()
    run = BackupRun.objects.create(
        storage_backend=settings.BACKUP_STORAGE_BACKEND,
        started_by=started_by,
    )
    object_key = f"postgres/{timezone.now():%Y/%m/%d}/mobipos-{run.id}.dump.fernet"
    try:
        connection_args, command_env = _postgres_connection_args(settings.BACKUP_SOURCE_DATABASE_URL)
        with tempfile.TemporaryDirectory() as temp_dir:
            dump_path = Path(temp_dir) / "database.dump"
            subprocess.run(
                ["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--file", str(dump_path), *connection_args],
                check=True,
                capture_output=True,
                timeout=1800,
                env=command_env,
            )
            encrypted = cipher.encrypt(dump_path.read_bytes())
            store_artifact(object_key, encrypted)
        run.status = BackupStatus.COMPLETED
        run.object_key = object_key
        run.size_bytes = len(encrypted)
        run.checksum_sha256 = hashlib.sha256(encrypted).hexdigest()
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "object_key", "size_bytes", "checksum_sha256", "completed_at", "updated_at"])
        return run
    except Exception as error:
        run.status = BackupStatus.FAILED
        run.error = str(error)[:1000]
        run.completed_at = timezone.now()
        run.save(update_fields=["status", "error", "completed_at", "updated_at"])
        raise BackupOperationError("Database backup failed. Review the backup run log.") from error


def _project_ref(database_url):
    parsed = urlparse(database_url)
    if parsed.hostname and parsed.hostname.startswith("db.") and parsed.hostname.endswith(".supabase.co"):
        return parsed.hostname.split(".")[1]
    username = parsed.username or ""
    return username.split(".", maxsplit=1)[1] if username.startswith("postgres.") else ""


def restore_database_backup(*, backup_run, confirm_project_ref):
    if not settings.BACKUP_RESTORE_DRILL_ENABLED:
        raise BackupOperationError("Restore drills are disabled.")
    target_url = settings.BACKUP_RESTORE_DATABASE_URL
    expected_ref = settings.BACKUP_RESTORE_PROJECT_REF
    actual_ref = _project_ref(target_url)
    source_ref = _project_ref(settings.BACKUP_SOURCE_DATABASE_URL)
    if not target_url or not expected_ref or confirm_project_ref != expected_ref or actual_ref != expected_ref:
        raise BackupOperationError("Restore target confirmation failed.")
    if target_url == settings.BACKUP_SOURCE_DATABASE_URL or (source_ref and source_ref == actual_ref):
        raise BackupOperationError("Restore target must not be the production database.")
    encrypted = read_artifact(backup_run.object_key)
    if hashlib.sha256(encrypted).hexdigest() != backup_run.checksum_sha256:
        raise BackupOperationError("Backup checksum validation failed.")
    try:
        plain = _validate_configuration().decrypt(encrypted)
    except InvalidToken as error:
        raise BackupOperationError("Backup decryption failed.") from error
    with tempfile.TemporaryDirectory() as temp_dir:
        dump_path = Path(temp_dir) / "database.dump"
        dump_path.write_bytes(plain)
        connection_args, command_env = _postgres_connection_args(target_url)
        subprocess.run(
            ["pg_restore", "--clean", "--if-exists", "--no-owner", "--no-privileges", *connection_args, str(dump_path)],
            check=True,
            capture_output=True,
            timeout=1800,
            env=command_env,
        )
    backup_run.status = BackupStatus.RESTORED
    backup_run.restored_at = timezone.now()
    backup_run.save(update_fields=["status", "restored_at", "updated_at"])
    return backup_run


def delete_backup(backup_run):
    if backup_run.object_key:
        delete_artifact(backup_run.object_key)
    backup_run.status = BackupStatus.DELETED
    backup_run.save(update_fields=["status", "updated_at"])
