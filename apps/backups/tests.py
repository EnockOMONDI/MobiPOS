import hashlib
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from django.test import override_settings

from apps.backups.models import BackupRun, BackupStatus
from apps.backups.services import BackupOperationError, create_database_backup, restore_database_backup


@pytest.mark.django_db
def test_backup_is_encrypted_checksummed_and_recorded(monkeypatch, tmp_path):
    key = Fernet.generate_key().decode("ascii")
    stored = {}

    def fake_run(command, **kwargs):
        Path(command[command.index("--file") + 1]).write_bytes(b"private database content")
        assert "postgresql://" not in " ".join(command)
        assert kwargs["env"]["PGPASSWORD"] == "source-password"

    monkeypatch.setattr("apps.backups.services.subprocess.run", fake_run)
    monkeypatch.setattr("apps.backups.services.store_artifact", lambda object_key, data: stored.update({object_key: data}))

    with override_settings(
        BACKUP_SOURCE_DATABASE_URL="postgresql://postgres:source-password@source.example/db?sslmode=require",
        BACKUP_ENCRYPTION_KEY=key,
        BACKUP_STORAGE_BACKEND="filesystem",
        BACKUP_STORAGE_DIR=tmp_path,
    ):
        run = create_database_backup()

    encrypted = stored[run.object_key]
    assert run.status == BackupStatus.COMPLETED
    assert encrypted != b"private database content"
    assert Fernet(key.encode("ascii")).decrypt(encrypted) == b"private database content"
    assert run.checksum_sha256 == hashlib.sha256(encrypted).hexdigest()


@pytest.mark.django_db
def test_restore_refuses_production_database():
    run = BackupRun.objects.create(
        status=BackupStatus.COMPLETED,
        storage_backend="supabase",
        object_key="backup.dump.fernet",
    )
    url = "postgresql://postgres@db.productionref.supabase.co:5432/postgres"
    with override_settings(
        BACKUP_RESTORE_DRILL_ENABLED=True,
        BACKUP_RESTORE_DATABASE_URL=url,
        BACKUP_RESTORE_PROJECT_REF="productionref",
        BACKUP_SOURCE_DATABASE_URL=url,
    ):
        with pytest.raises(BackupOperationError, match="must not be the production"):
            restore_database_backup(backup_run=run, confirm_project_ref="productionref")


@pytest.mark.django_db
def test_restore_refuses_pooler_alias_for_production_project():
    run = BackupRun.objects.create(
        status=BackupStatus.COMPLETED,
        storage_backend="supabase",
        object_key="backup.dump.fernet",
    )
    with override_settings(
        BACKUP_RESTORE_DRILL_ENABLED=True,
        BACKUP_RESTORE_DATABASE_URL=(
            "postgresql://postgres.productionref@aws-1-eu-west-1.pooler.supabase.com:6543/postgres"
        ),
        BACKUP_RESTORE_PROJECT_REF="productionref",
        BACKUP_SOURCE_DATABASE_URL=(
            "postgresql://postgres@db.productionref.supabase.co:5432/postgres"
        ),
    ):
        with pytest.raises(BackupOperationError, match="must not be the production"):
            restore_database_backup(backup_run=run, confirm_project_ref="productionref")


@pytest.mark.django_db
def test_restore_requires_exact_target_project_confirmation():
    run = BackupRun.objects.create(
        status=BackupStatus.COMPLETED,
        storage_backend="supabase",
        object_key="backup.dump.fernet",
    )
    with override_settings(
        BACKUP_RESTORE_DRILL_ENABLED=True,
        BACKUP_RESTORE_DATABASE_URL="postgresql://postgres@db.restoreproject.supabase.co:5432/postgres",
        BACKUP_RESTORE_PROJECT_REF="restoreproject",
        BACKUP_SOURCE_DATABASE_URL="postgresql://postgres@db.productionref.supabase.co:5432/postgres",
    ):
        with pytest.raises(BackupOperationError, match="confirmation failed"):
            restore_database_backup(backup_run=run, confirm_project_ref="wrong-project")
