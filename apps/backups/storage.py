import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings


class BackupStorageError(RuntimeError):
    pass


def _supabase_request(*, method, object_key="", data=None, content_type="application/octet-stream"):
    object_path = f"/{quote(object_key)}" if object_key else ""
    url = f"{settings.BACKUP_SUPABASE_URL}/storage/v1/object/{quote(settings.BACKUP_SUPABASE_BUCKET)}{object_path}"
    request = Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {settings.BACKUP_SUPABASE_SERVICE_KEY}",
            "apikey": settings.BACKUP_SUPABASE_SERVICE_KEY,
            "Content-Type": content_type,
            "x-upsert": "false",
        },
    )
    try:
        with urlopen(request, timeout=120) as response:
            return response.read()
    except HTTPError as error:
        raise BackupStorageError(f"Backup storage returned HTTP {error.code}.") from error
    except (URLError, TimeoutError, OSError) as error:
        raise BackupStorageError("Backup storage could not be reached.") from error


def store_artifact(object_key, encrypted_data):
    if settings.BACKUP_STORAGE_BACKEND == "supabase":
        _supabase_request(method="POST", object_key=object_key, data=encrypted_data)
        return
    path = Path(settings.BACKUP_STORAGE_DIR) / object_key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encrypted_data)


def read_artifact(object_key):
    if settings.BACKUP_STORAGE_BACKEND == "supabase":
        return _supabase_request(method="GET", object_key=object_key)
    return (Path(settings.BACKUP_STORAGE_DIR) / object_key).read_bytes()


def delete_artifact(object_key):
    if settings.BACKUP_STORAGE_BACKEND == "supabase":
        _supabase_request(
            method="DELETE",
            data=json.dumps({"prefixes": [object_key]}).encode("utf-8"),
            content_type="application/json",
        )
        return
    path = Path(settings.BACKUP_STORAGE_DIR) / object_key
    path.unlink(missing_ok=True)
