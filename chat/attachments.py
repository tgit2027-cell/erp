"""Checks for files shared in chat: allowed types, size, and that content matches the type."""

import hashlib
from pathlib import Path

from rest_framework.exceptions import ValidationError

MAX_FILE_BYTES = 25 * 1024 * 1024
MAX_FILES_PER_MESSAGE = 5
ZIP = (b"PK\x03\x04",)
OLE = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",)
TEXT = None  # Checked as readable text instead of a signature.
ALLOWED = {
    ".pdf": ("application/pdf", (b"%PDF-",)),
    ".png": ("image/png", (b"\x89PNG\r\n\x1a\n",)),
    ".jpg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".jpeg": ("image/jpeg", (b"\xff\xd8\xff",)),
    ".gif": ("image/gif", (b"GIF87a", b"GIF89a")),
    ".webp": ("image/webp", (b"RIFF",)),
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", ZIP),
    ".xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ZIP),
    ".pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", ZIP),
    ".doc": ("application/msword", OLE),
    ".xls": ("application/vnd.ms-excel", OLE),
    ".ppt": ("application/vnd.ms-powerpoint", OLE),
    ".txt": ("text/plain", TEXT),
    ".csv": ("text/csv", TEXT),
}
# Pictures that may be shown inside the chat; everything else is only downloaded.
INLINE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
ALLOWED_LABEL = "PDF, Word, Excel, PowerPoint, pictures (PNG, JPG, GIF, WEBP), text or CSV"


def _readable(head):
    # A cut in the middle of a multi-byte letter at the 4 KB edge is fine.
    for end in (len(head), len(head) - 1, len(head) - 2, len(head) - 3):
        try:
            head[:max(end, 0)].decode("utf-8")
            return True
        except UnicodeDecodeError:
            continue
    return False


def check_upload(upload):
    """Return (name, content_type, size, sha256) or raise a plain-language error."""
    name = Path(getattr(upload, "name", "") or "").name.strip() or "file"
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED:
        raise ValidationError({"files": f"“{name}” cannot be shared. Allowed: {ALLOWED_LABEL}."})
    size = getattr(upload, "size", 0) or 0
    if size <= 0:
        raise ValidationError({"files": f"“{name}” is empty."})
    if size > MAX_FILE_BYTES:
        raise ValidationError({"files": f"“{name}” is larger than 25 MB."})
    content_type, signatures = ALLOWED[suffix]
    upload.seek(0)
    head = upload.read(4096)
    if signatures is TEXT:
        if b"\x00" in head or not _readable(head):
            raise ValidationError({"files": f"“{name}” is not a plain text file."})
    elif not any(head.startswith(signature) for signature in signatures):
        raise ValidationError({"files": f"The content of “{name}” does not match its type."})
    if suffix == ".webp" and head[8:12] != b"WEBP":
        raise ValidationError({"files": f"The content of “{name}” does not match its type."})
    upload.seek(0)
    digest = hashlib.sha256()
    for chunk in upload.chunks():
        digest.update(chunk)
    upload.seek(0)
    return name[:200], content_type, size, digest.hexdigest()


def attachment_row(attachment):
    return {
        "id": attachment.pk,
        "name": attachment.original_name,
        "size": attachment.size,
        "content_type": attachment.content_type,
        "is_picture": attachment.content_type in INLINE_TYPES,
    }
