"""Read an uploaded document with Claude and return structured data (shared by every 'scan a document' feature).

The caller supplies what to look for (instructions) and the shape of the answer
(a JSON schema). This module checks the file, sends it to the Anthropic API,
and returns the parsed JSON. Nothing is saved: the caller shows the result as
a draft that a person checks before anything is recorded.

Turned on by the ANTHROPIC_API_KEY environment variable. Without it, every
call raises DocumentReaderUnavailable so the page can say so plainly.
"""

import base64
import json
import os

from .transaction_services import InvalidTransaction

MODEL = os.environ.get("ERP_DOCUMENT_AI_MODEL", "claude-opus-5-5")
MAX_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 400_000
PDF = "application/pdf"
IMAGES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
TEXT_EXTENSIONS = {".csv", ".txt", ".tsv"}
EXTENSION_TYPES = {".pdf": PDF, ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                   ".webp": "image/webp", ".gif": "image/gif"}


class DocumentReaderUnavailable(InvalidTransaction):
    """No API key is configured, or the AI service could not be reached."""


def is_available():
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def _client():
    """Built lazily so the ERP runs (and tests pass) without the package or a key."""
    if not is_available():
        raise DocumentReaderUnavailable("Reading documents with AI is not switched on here. Add the lines by hand.")
    try:
        import anthropic
    except ImportError:  # pragma: no cover - the package is in requirements.txt
        raise DocumentReaderUnavailable("Reading documents with AI is not installed here. Add the lines by hand.") from None
    return anthropic.Anthropic()


def _content_block(upload):
    name = (getattr(upload, "name", "") or "").lower()
    extension = name[name.rfind("."):] if "." in name else ""
    size = getattr(upload, "size", None)
    if size is not None and size > MAX_BYTES:
        raise InvalidTransaction("The file is larger than 10 MB. Upload a smaller file or split the statement.")
    data = upload.read()
    if not data:
        raise InvalidTransaction("The file is empty.")
    if len(data) > MAX_BYTES:
        raise InvalidTransaction("The file is larger than 10 MB. Upload a smaller file or split the statement.")
    if extension in TEXT_EXTENSIONS:
        for encoding in ("utf-8-sig", "cp1252"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if len(text) > MAX_TEXT_CHARS:
            raise InvalidTransaction("The file has too much text to read at once. Split it into smaller files.")
        return {"type": "text", "text": f"<document name=\"{name}\">\n{text}\n</document>"}
    media_type = EXTENSION_TYPES.get(extension)
    if media_type is None:
        raise InvalidTransaction("Upload a PDF, a photo (PNG or JPEG) or a CSV file.")
    encoded = base64.standard_b64encode(data).decode("ascii")
    if media_type == PDF:
        return {"type": "document", "source": {"type": "base64", "media_type": PDF, "data": encoded}}
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": encoded}}


def read_document(upload, *, instructions, schema, max_tokens=32000):
    """Send one file with instructions and return the JSON answer as a dict."""
    return ask([_content_block(upload)], instructions=instructions, schema=schema, max_tokens=max_tokens)


def ask(blocks, *, instructions, schema, max_tokens=32000):
    """Send prepared content blocks (document, image or text) with instructions; return the JSON answer."""
    client = _client()
    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=max_tokens,
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
            messages=[{"role": "user", "content": [*blocks, {"type": "text", "text": instructions}]}],
        ) as stream:
            response = stream.get_final_message()
    except Exception as error:  # the SDK's connection, rate-limit and API errors
        status = getattr(error, "status_code", None)
        detail = f" (error {status})" if status else ""
        raise DocumentReaderUnavailable(
            f"The AI reader could not be reached{detail}. Try again in a minute, or add the lines by hand."
        ) from error
    if response.stop_reason == "refusal":
        raise InvalidTransaction("The AI reader declined to read this file. Add the lines by hand.")
    if response.stop_reason == "max_tokens":
        raise InvalidTransaction("The file is too long to read in one go. Split it into smaller files.")
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        return json.loads(text)
    except ValueError:
        raise InvalidTransaction("The AI reader returned something unreadable. Try again, or add the lines by hand.") from None
