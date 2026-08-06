import hashlib
import hmac
import json
import mimetypes
import time
import uuid
from pathlib import PurePosixPath
from urllib import error, parse, request

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.files.storage import Storage
from django.utils.text import get_valid_filename


class UploadcareMediaStorage(Storage):
    """Django storage backend for server-side Uploadcare uploads."""

    upload_endpoint = "https://upload.uploadcare.com/base/"

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.public_key = getattr(settings, "UPLOADCARE_PUBLIC_KEY", "")
        self.secret_key = getattr(settings, "UPLOADCARE_SECRET_KEY", "")
        self.store = getattr(settings, "UPLOADCARE_STORE", "1")
        self.signed_uploads = getattr(settings, "UPLOADCARE_SIGNED_UPLOADS", False)
        self.signed_delivery = getattr(settings, "UPLOADCARE_SIGNED_DELIVERY", False)
        self.signing_secret = getattr(settings, "UPLOADCARE_SIGNING_SECRET", "")
        self.signed_url_ttl = int(getattr(settings, "UPLOADCARE_SIGNED_URL_TTL", 300))
        self.cdn_base_url = getattr(settings, "UPLOADCARE_CDN_BASE_URL", "https://ucarecdn.com").rstrip("/")

        if not self.public_key:
            raise ImproperlyConfigured("UPLOADCARE_PUBLIC_KEY is required for Uploadcare media storage.")
        if self.signed_uploads and not self.secret_key:
            raise ImproperlyConfigured("UPLOADCARE_SECRET_KEY is required when UPLOADCARE_SIGNED_UPLOADS is enabled.")
        if self.signed_delivery and not self.signing_secret:
            raise ImproperlyConfigured("UPLOADCARE_SIGNING_SECRET is required when UPLOADCARE_SIGNED_DELIVERY is enabled.")

    def _save(self, name, content):
        original_name = get_valid_filename(PurePosixPath(name).name or "upload")
        payload, content_type = self._multipart_payload(name=original_name, content=content)
        req = request.Request(
            self.upload_endpoint,
            data=payload,
            headers={"Content-Type": content_type},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=getattr(settings, "UPLOADCARE_UPLOAD_TIMEOUT", 30)) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise OSError(f"Uploadcare upload failed with status {exc.code}: {details}") from exc
        except error.URLError as exc:
            raise OSError(f"Uploadcare upload failed: {exc.reason}") from exc

        file_uuid = response_data.get("file") or response_data.get(original_name)
        if not file_uuid and response_data:
            file_uuid = next(iter(response_data.values()))
        if not file_uuid:
            raise OSError(f"Uploadcare upload response did not include a file UUID: {response_data}")
        return f"uploadcare/{file_uuid}/{original_name}"

    def exists(self, name):
        return False

    def delete(self, name):
        # Deletion requires REST API credentials and should usually be audited.
        # Keep this as a no-op until delete policy is explicitly defined.
        return None

    def url(self, name):
        file_uuid, filename = self._split_name(name)
        path = f"/{file_uuid}/"
        if filename:
            path = f"{path}{parse.quote(filename)}"
        url = f"{self.cdn_base_url}{path}"
        if not self.signed_delivery:
            return url
        return f"{url}?token={parse.quote(self._signed_delivery_token(file_uuid), safe='=~/*')}"

    def size(self, name):
        return 0

    def _split_name(self, name):
        parts = PurePosixPath(name).parts
        if len(parts) >= 2 and parts[0] == "uploadcare":
            return parts[1], parts[2] if len(parts) > 2 else ""
        if parts:
            return parts[0], parts[-1] if len(parts) > 1 else ""
        return name, ""

    def _multipart_payload(self, name, content):
        boundary = f"----MobiPOSUploadcare{uuid.uuid4().hex}"
        fields = {
            "UPLOADCARE_PUB_KEY": self.public_key,
            "UPLOADCARE_STORE": self.store,
        }
        if self.signed_uploads:
            expires_at = str(int(time.time()) + 30 * 60)
            signature = hmac.new(self.secret_key.encode("utf-8"), expires_at.encode("utf-8"), hashlib.sha256).hexdigest()
            fields.update({"expire": expires_at, "signature": signature})

        body = bytearray()
        for key, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode("utf-8"))

        file_bytes = b"".join(chunk for chunk in content.chunks())
        detected_type = getattr(content, "content_type", "") or mimetypes.guess_type(name)[0] or "application/octet-stream"
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode("utf-8"))
        body.extend(f"Content-Type: {detected_type}\r\n\r\n".encode("utf-8"))
        body.extend(file_bytes)
        body.extend(f"\r\n--{boundary}--\r\n".encode("utf-8"))
        return bytes(body), f"multipart/form-data; boundary={boundary}"

    def _signed_delivery_token(self, file_uuid):
        expires_at = int(time.time()) + self.signed_url_ttl
        acl = f"/{file_uuid}/*"
        token_body = f"exp={expires_at}~acl={acl}"
        try:
            secret = bytes.fromhex(self.signing_secret)
        except ValueError as exc:
            raise ImproperlyConfigured("UPLOADCARE_SIGNING_SECRET must be a hex-encoded signing secret.") from exc
        digest = hmac.new(secret, token_body.encode("utf-8"), hashlib.sha256).hexdigest()
        return f"{token_body}~hmac={digest}"
