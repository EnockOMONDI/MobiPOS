import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile

from config.uploads import validate_uploaded_content


def test_upload_validation_rejects_spoofed_pdf_contents():
    upload = SimpleUploadedFile("identity.pdf", b"not a pdf", content_type="application/pdf")

    with pytest.raises(ValidationError, match="contents do not match"):
        validate_uploaded_content(
            upload,
            allowed_extensions={".pdf"},
            max_size=1024,
            label="Document",
        )


def test_upload_validation_accepts_pdf_signature_and_restores_stream_position():
    upload = SimpleUploadedFile("identity.pdf", b"%PDF-1.7\ncontent", content_type="application/pdf")

    assert validate_uploaded_content(
        upload,
        allowed_extensions={".pdf"},
        max_size=1024,
        label="Document",
    ) is upload
    assert upload.read().startswith(b"%PDF-1.7")


def test_upload_validation_rejects_binary_file_disguised_as_csv():
    upload = SimpleUploadedFile("stock.csv", b"serial\x00\xff", content_type="text/csv")

    with pytest.raises(ValidationError, match="contents do not match"):
        validate_uploaded_content(
            upload,
            allowed_extensions={".csv"},
            max_size=1024,
            label="Stock intake file",
        )
