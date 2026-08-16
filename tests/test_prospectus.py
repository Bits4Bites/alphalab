import io
import json
import pathlib
from unittest import mock

import fastapi
import pytest
from starlette.datastructures import Headers

from app.routers import ipo_analyzer
from app.services import prospectus


def _upload_file(
    content: bytes,
    *,
    filename: str = "prospectus.pdf",
    content_type: str = "application/pdf",
) -> fastapi.UploadFile:
    return fastapi.UploadFile(
        file=io.BytesIO(content),
        filename=filename,
        headers=Headers({"content-type": content_type}),
    )


@pytest.mark.asyncio
async def test_save_pdf_assigns_unique_id_and_persists_file(tmp_path: pathlib.Path) -> None:
    content = b"%PDF-1.7\nprospectus"

    first_id = await prospectus.save_pdf(_upload_file(content), upload_directory=tmp_path)
    second_id = await prospectus.save_pdf(_upload_file(content), upload_directory=tmp_path)

    assert first_id != second_id
    assert len(first_id) == 32
    assert (tmp_path / f"{first_id}.pdf").read_bytes() == content


@pytest.mark.asyncio
async def test_save_pdf_rejects_non_pdf_extension(tmp_path: pathlib.Path) -> None:
    with pytest.raises(prospectus.InvalidProspectusError, match="Only PDF"):
        await prospectus.save_pdf(
            _upload_file(b"%PDF-1.7", filename="prospectus.txt"),
            upload_directory=tmp_path,
        )


@pytest.mark.asyncio
async def test_save_pdf_rejects_non_pdf_media_type(tmp_path: pathlib.Path) -> None:
    with pytest.raises(prospectus.InvalidProspectusError, match="Only PDF"):
        await prospectus.save_pdf(
            _upload_file(b"%PDF-1.7", content_type="text/plain"),
            upload_directory=tmp_path,
        )


@pytest.mark.asyncio
async def test_save_pdf_rejects_invalid_pdf_signature(tmp_path: pathlib.Path) -> None:
    with pytest.raises(prospectus.InvalidProspectusError, match="not a valid PDF"):
        await prospectus.save_pdf(
            _upload_file(b"not a pdf"),
            upload_directory=tmp_path,
        )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_save_pdf_rejects_file_over_size_limit(tmp_path: pathlib.Path) -> None:
    with pytest.raises(prospectus.ProspectusTooLargeError, match="20 MB"):
        await prospectus.save_pdf(
            _upload_file(b"%PDF-1.7\ncontent"),
            upload_directory=tmp_path,
            max_size_bytes=8,
        )

    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_convert_pdf_to_markdown_uses_stored_document(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    document_id = "a" * 32
    pdf_path = tmp_path / f"{document_id}.pdf"
    pdf_path.write_bytes(b"%PDF-1.7")
    to_markdown = mock.Mock(return_value="# Prospectus\n\nCompany details")
    monkeypatch.setattr(prospectus.pymupdf4llm, "to_markdown", to_markdown)

    markdown = await prospectus.convert_pdf_to_markdown(document_id, upload_directory=tmp_path)

    assert markdown == "# Prospectus\n\nCompany details"
    to_markdown.assert_called_once_with(
        str(pdf_path),
        header=False,
        footer=False,
        use_ocr=False,
        ignore_images=True,
        write_images=False,
        embed_images=False,
    )


@pytest.mark.asyncio
async def test_convert_pdf_to_markdown_rejects_unknown_document(tmp_path: pathlib.Path) -> None:
    with pytest.raises(prospectus.ProspectusNotFoundError, match="could not be found"):
        await prospectus.convert_pdf_to_markdown("../missing", upload_directory=tmp_path)


def test_delete_pdf_removes_stored_document(tmp_path: pathlib.Path) -> None:
    document_id = "c" * 32
    pdf_path = tmp_path / f"{document_id}.pdf"
    pdf_path.write_bytes(b"%PDF-1.7")

    prospectus.delete_pdf(document_id, upload_directory=tmp_path)

    assert not pdf_path.exists()


@pytest.mark.asyncio
async def test_upload_endpoint_returns_document_id(monkeypatch: pytest.MonkeyPatch) -> None:
    save_pdf = mock.AsyncMock(return_value="d" * 32)
    monkeypatch.setattr(ipo_analyzer.prospectus, "save_pdf", save_pdf)

    response = await ipo_analyzer.upload_prospectus(
        _upload_file(b"%PDF-1.7"),
        _user={},
    )

    assert response.status_code == 200
    assert json.loads(response.body) == {"document_id": "d" * 32}


@pytest.mark.asyncio
async def test_upload_endpoint_returns_413_for_oversized_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    sensitive_error = "Prospectus PDF must be 20 MB or smaller: client-value"
    save_pdf = mock.AsyncMock(side_effect=prospectus.ProspectusTooLargeError(sensitive_error))
    monkeypatch.setattr(ipo_analyzer.prospectus, "save_pdf", save_pdf)

    response = await ipo_analyzer.upload_prospectus(
        _upload_file(b"%PDF-1.7"),
        _user={},
    )

    assert response.status_code == 413
    assert json.loads(response.body)["detail"] == "Prospectus file is too large"
    assert sensitive_error not in response.body.decode()


@pytest.mark.asyncio
async def test_upload_endpoint_returns_generic_415_for_invalid_pdf(monkeypatch: pytest.MonkeyPatch) -> None:
    sensitive_error = "Invalid filename: client-value.pdf"
    save_pdf = mock.AsyncMock(side_effect=prospectus.InvalidProspectusError(sensitive_error))
    monkeypatch.setattr(ipo_analyzer.prospectus, "save_pdf", save_pdf)

    response = await ipo_analyzer.upload_prospectus(
        _upload_file(b"not a pdf"),
        _user={},
    )

    assert response.status_code == 415
    assert json.loads(response.body)["detail"] == "Prospectus file format is invalid or not supported"
    assert sensitive_error not in response.body.decode()
