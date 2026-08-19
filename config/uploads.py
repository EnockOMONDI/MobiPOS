from pathlib import Path

from django.core.exceptions import ValidationError


IMAGE_SIGNATURES = {
    ".gif": lambda data: data.startswith((b"GIF87a", b"GIF89a")),
    ".jpeg": lambda data: data.startswith(b"\xff\xd8\xff"),
    ".jpg": lambda data: data.startswith(b"\xff\xd8\xff"),
    ".png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
    ".webp": lambda data: data.startswith(b"RIFF") and data[8:12] == b"WEBP",
}


def validate_uploaded_content(upload, *, allowed_extensions, max_size, label="File"):
    """Validate size, extension, and basic file signature without trusting MIME."""
    extension = Path(upload.name or "").suffix.lower()
    if extension not in allowed_extensions:
        allowed = ", ".join(sorted(allowed_extensions))
        raise ValidationError(f"Unsupported {label.lower()} type. Allowed file types: {allowed}.")
    if upload.size > max_size:
        raise ValidationError(f"{label} must be {max_size // (1024 * 1024)} MB or smaller.")

    position = upload.tell() if hasattr(upload, "tell") else 0
    header = upload.read(min(max_size, 8192))
    if hasattr(upload, "seek"):
        upload.seek(position)

    valid = False
    if extension == ".pdf":
        valid = header.startswith(b"%PDF-")
    elif extension in IMAGE_SIGNATURES:
        valid = IMAGE_SIGNATURES[extension](header)
    elif extension in {".xlsx", ".xlsm"}:
        valid = header.startswith(b"PK\x03\x04")
    elif extension in {".csv", ".tsv", ".txt"}:
        try:
            header.decode("utf-8-sig")
            valid = b"\x00" not in header
        except UnicodeDecodeError:
            valid = False

    if not valid:
        raise ValidationError(
            f"The {label.lower()} contents do not match the {extension or 'selected'} file type. "
            "Choose the original file and try again."
        )
    return upload
