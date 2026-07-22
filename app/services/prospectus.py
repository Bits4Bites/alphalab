import asyncio
import pathlib
import re
import uuid

import fastapi
import pymupdf4llm

UPLOAD_DIRECTORY = pathlib.Path("uploads")
MAX_UPLOAD_SIZE_BYTES = 20 * 1024 * 1024
_READ_CHUNK_SIZE = 1024 * 1024
_PDF_HEADER_SCAN_BYTES = 1024
_DOCUMENT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


class InvalidProspectusError(ValueError):
    pass


class ProspectusTooLargeError(ValueError):
    pass


class ProspectusNotFoundError(ValueError):
    pass


class ProspectusConversionError(RuntimeError):
    pass


async def save_pdf(
    upload: fastapi.UploadFile,
    *,
    upload_directory: pathlib.Path = UPLOAD_DIRECTORY,
    max_size_bytes: int = MAX_UPLOAD_SIZE_BYTES,
) -> str:
    """Validate and persist an uploaded PDF, returning its generated document ID."""
    filename = upload.filename or ""
    content_type = (upload.content_type or "").lower()
    if pathlib.Path(filename).suffix.lower() != ".pdf":
        raise InvalidProspectusError("Only PDF files are supported.")
    if content_type and content_type not in {"application/pdf", "application/x-pdf"}:
        raise InvalidProspectusError("Only PDF files are supported.")

    upload_directory.mkdir(parents=True, exist_ok=True)
    document_id = uuid.uuid4().hex
    destination = upload_directory / f"{document_id}.pdf"
    total_size = 0
    header = bytearray()

    saved = False
    try:
        with destination.open("xb") as output:
            while chunk := await upload.read(_READ_CHUNK_SIZE):
                total_size += len(chunk)
                if total_size > max_size_bytes:
                    raise ProspectusTooLargeError("Prospectus PDF must be 20 MB or smaller.")

                if len(header) < _PDF_HEADER_SCAN_BYTES:
                    remaining = _PDF_HEADER_SCAN_BYTES - len(header)
                    header.extend(chunk[:remaining])
                output.write(chunk)

        if not total_size or b"%PDF-" not in header:
            raise InvalidProspectusError("The uploaded file is not a valid PDF.")
        saved = True
    finally:
        if not saved:
            destination.unlink(missing_ok=True)
        await upload.close()

    return document_id


def get_pdf_path(
    document_id: str,
    *,
    upload_directory: pathlib.Path = UPLOAD_DIRECTORY,
) -> pathlib.Path:
    """Resolve a document ID to a stored PDF without allowing path traversal."""
    normalized_id = document_id.strip().lower()
    if not _DOCUMENT_ID_PATTERN.fullmatch(normalized_id):
        raise ProspectusNotFoundError("The uploaded prospectus could not be found.")

    path = upload_directory / f"{normalized_id}.pdf"
    if not path.is_file():
        raise ProspectusNotFoundError("The uploaded prospectus could not be found.")
    return path


def delete_pdf(
    document_id: str,
    *,
    upload_directory: pathlib.Path = UPLOAD_DIRECTORY,
) -> None:
    """Delete a stored prospectus after its analysis lifecycle finishes."""
    path = get_pdf_path(document_id, upload_directory=upload_directory)
    path.unlink()


async def convert_pdf_to_markdown(
    document_id: str,
    *,
    upload_directory: pathlib.Path = UPLOAD_DIRECTORY,
) -> str:
    """Convert a stored prospectus PDF to Markdown without blocking the event loop."""
    path = get_pdf_path(document_id, upload_directory=upload_directory)
    try:
        markdown = await asyncio.to_thread(
            pymupdf4llm.to_markdown,
            str(path),
            header=False,
            footer=False,
            use_ocr=False,
            ignore_images=True,
            write_images=False,
            embed_images=False,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise ProspectusConversionError("The uploaded prospectus could not be converted.") from exc

    if not isinstance(markdown, str) or not markdown.strip():
        raise ProspectusConversionError("The uploaded prospectus did not contain readable text.")
    return markdown.strip()
