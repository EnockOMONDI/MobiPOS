import json

from django.test import override_settings

from apps.backups.storage import delete_artifact


@override_settings(
    BACKUP_STORAGE_BACKEND="supabase",
    BACKUP_SUPABASE_URL="https://backup-project.supabase.co",
    BACKUP_SUPABASE_SERVICE_KEY="service-key",
    BACKUP_SUPABASE_BUCKET="database-backups",
)
def test_supabase_backup_deletion_uses_storage_remove_endpoint(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return b"[]"

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("apps.backups.storage.urlopen", fake_urlopen)

    delete_artifact("postgres/2026/08/14/backup.dump.fernet")

    request = captured["request"]
    assert request.full_url == "https://backup-project.supabase.co/storage/v1/object/database-backups"
    assert request.method == "DELETE"
    assert json.loads(request.data) == {
        "prefixes": ["postgres/2026/08/14/backup.dump.fernet"],
    }
    assert request.headers["Content-type"] == "application/json"
