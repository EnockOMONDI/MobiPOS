import json

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
