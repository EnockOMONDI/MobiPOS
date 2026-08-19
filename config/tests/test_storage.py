import json
from io import BytesIO
from urllib import error

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.core.files.base import ContentFile
from django.test import override_settings

from config import storage
from config.storage import UploadcareMediaStorage


@override_settings(UPLOADCARE_PUBLIC_KEY="")
def test_uploadcare_storage_requires_public_key():
    with pytest.raises(ImproperlyConfigured):
        UploadcareMediaStorage()


@override_settings(
    UPLOADCARE_PUBLIC_KEY="public-key",
    UPLOADCARE_SIGNED_DELIVERY=False,
    UPLOADCARE_CDN_BASE_URL="https://ucarecdn.com",
)
def test_uploadcare_storage_generates_public_cdn_url():
    backend = UploadcareMediaStorage()

    assert backend.url("uploadcare/11111111-1111-1111-1111-111111111111/receipt.pdf") == (
        "https://ucarecdn.com/11111111-1111-1111-1111-111111111111/receipt.pdf"
    )


@override_settings(
    UPLOADCARE_PUBLIC_KEY="public-key",
    UPLOADCARE_SIGNED_DELIVERY=True,
    UPLOADCARE_SIGNING_SECRET="0" * 64,
    UPLOADCARE_CDN_BASE_URL="https://secure.example.com",
    UPLOADCARE_SIGNED_URL_TTL=300,
)
def test_uploadcare_storage_generates_signed_delivery_url():
    backend = UploadcareMediaStorage()

    url = backend.url("uploadcare/11111111-1111-1111-1111-111111111111/id-card.png")

    assert url.startswith("https://secure.example.com/11111111-1111-1111-1111-111111111111/id-card.png?token=")
    assert "acl=/11111111-1111-1111-1111-111111111111/*" in url
    assert "hmac=" in url


@override_settings(
    UPLOADCARE_PUBLIC_KEY="public-key",
    UPLOADCARE_STORE="1",
    UPLOADCARE_SIGNED_UPLOADS=False,
    UPLOADCARE_UPLOAD_TIMEOUT=30,
)
def test_uploadcare_storage_saves_uploaded_file_without_real_network(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps({"file": "22222222-2222-2222-2222-222222222222"}).encode("utf-8")

    def fake_urlopen(req, timeout):
        captured["request"] = req
        captured["timeout"] = timeout
        captured["body"] = req.data
        return Response()

    monkeypatch.setattr(storage.request, "urlopen", fake_urlopen)
    backend = UploadcareMediaStorage()

    saved_name = backend.save("purchase invoices/Invoice 001.pdf", ContentFile(b"demo-pdf"))

    assert saved_name == "uploadcare/22222222-2222-2222-2222-222222222222/Invoice_001.pdf"
    assert captured["timeout"] == 30
    assert b'Content-Disposition: form-data; name="UPLOADCARE_PUB_KEY"' in captured["body"]
    assert b'Content-Disposition: form-data; name="UPLOADCARE_STORE"' in captured["body"]
    assert b'Content-Disposition: form-data; name="file"; filename="Invoice_001.pdf"' in captured["body"]
    assert b"demo-pdf" in captured["body"]


@override_settings(
    UPLOADCARE_PUBLIC_KEY="public-key",
    UPLOADCARE_SECRET_KEY="secret-key",
    UPLOADCARE_UPLOAD_TIMEOUT=17,
)
def test_uploadcare_storage_deletes_file_with_signed_rest_request(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    def fake_urlopen(req, timeout):
        captured["request"] = req
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr(storage.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(storage, "formatdate", lambda **_kwargs: "Sun, 16 Aug 2026 12:00:00 GMT")
    backend = UploadcareMediaStorage()

    backend.delete("uploadcare/22222222-2222-2222-2222-222222222222/receipt.pdf")

    req = captured["request"]
    assert req.method == "DELETE"
    assert req.full_url == "https://api.uploadcare.com/files/22222222-2222-2222-2222-222222222222/storage/"
    assert req.get_header("Accept") == "application/vnd.uploadcare-v0.7+json"
    assert req.get_header("Date") == "Sun, 16 Aug 2026 12:00:00 GMT"
    assert req.get_header("Authorization").startswith("Uploadcare public-key:")
    assert "secret-key" not in req.get_header("Authorization")
    assert captured["timeout"] == 17


@override_settings(
    UPLOADCARE_PUBLIC_KEY="public-key",
    UPLOADCARE_SECRET_KEY="secret-key",
)
def test_uploadcare_storage_delete_is_idempotent_when_file_is_missing(monkeypatch):
    def fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, 404, "Not Found", {}, BytesIO(b'{"detail":"Not found"}'))

    monkeypatch.setattr(storage.request, "urlopen", fake_urlopen)

    assert UploadcareMediaStorage().delete("uploadcare/22222222-2222-2222-2222-222222222222/file.pdf") is None


@override_settings(
    UPLOADCARE_PUBLIC_KEY="public-key",
    UPLOADCARE_SECRET_KEY="secret-key",
)
def test_uploadcare_storage_delete_surfaces_provider_failure(monkeypatch):
    def fake_urlopen(req, timeout):
        raise error.HTTPError(req.full_url, 401, "Unauthorized", {}, BytesIO(b'{"detail":"Denied"}'))

    monkeypatch.setattr(storage.request, "urlopen", fake_urlopen)

    with pytest.raises(OSError, match="Uploadcare deletion failed with status 401"):
        UploadcareMediaStorage().delete("uploadcare/22222222-2222-2222-2222-222222222222/file.pdf")


@override_settings(
    UPLOADCARE_PUBLIC_KEY="public-key",
    UPLOADCARE_SECRET_KEY="secret-key",
)
def test_uploadcare_storage_delete_rejects_invalid_file_name():
    with pytest.raises(ValueError, match="Invalid Uploadcare file name"):
        UploadcareMediaStorage().delete("uploadcare/not-a-uuid/file.pdf")
