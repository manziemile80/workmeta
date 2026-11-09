"""Unit tests for upload validation, extraction and chunking."""

from __future__ import annotations

import io

import pytest
from docx import Document as DocxDocument
from pypdf import PdfWriter

from app.services.document_text import (
    DOCX_MIME,
    PDF_MIME,
    TEXT_MIME,
    DocumentValidationError,
    TextPage,
    chunk_pages,
    clean_text,
    extract_pages,
    validate_upload,
)

MAX_BYTES = 1024 * 1024


def _docx_bytes(*paragraphs: str) -> bytes:
    document = DocxDocument()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_text_upload_is_detected_from_content() -> None:
    assert (
        validate_upload(
            filename="notes.txt",
            declared_mime="text/plain",
            data=b"Invoice steps",
            max_bytes=MAX_BYTES,
        )
        == TEXT_MIME
    )


def test_markdown_extension_accepts_detected_text() -> None:
    assert (
        validate_upload(
            filename="guide.md",
            declared_mime="text/markdown",
            data=b"# Heading\n\nBody",
            max_bytes=MAX_BYTES,
        )
        == TEXT_MIME
    )


def test_pdf_magic_bytes_are_required() -> None:
    assert (
        validate_upload(
            filename="guide.pdf",
            declared_mime="application/pdf",
            data=b"%PDF-1.7\n%fake body",
            max_bytes=MAX_BYTES,
        )
        == PDF_MIME
    )


def test_mislabelled_extension_is_rejected() -> None:
    with pytest.raises(DocumentValidationError, match="does not match"):
        validate_upload(
            filename="guide.txt",
            declared_mime="text/plain",
            data=b"%PDF-1.7\n%fake body",
            max_bytes=MAX_BYTES,
        )


def test_unsupported_extension_is_rejected() -> None:
    with pytest.raises(DocumentValidationError, match="Unsupported file type"):
        validate_upload(
            filename="payload.exe",
            declared_mime="application/octet-stream",
            data=b"MZ\x90\x00",
            max_bytes=MAX_BYTES,
        )


def test_empty_and_oversized_uploads_are_rejected() -> None:
    with pytest.raises(DocumentValidationError, match="empty"):
        validate_upload(
            filename="empty.txt",
            declared_mime="text/plain",
            data=b"",
            max_bytes=MAX_BYTES,
        )
    with pytest.raises(DocumentValidationError, match="larger than"):
        validate_upload(
            filename="big.txt",
            declared_mime="text/plain",
            data=b"a" * 12,
            max_bytes=10,
        )


def test_binary_data_is_rejected_as_text() -> None:
    with pytest.raises(DocumentValidationError):
        validate_upload(
            filename="binary.txt",
            declared_mime="text/plain",
            data=b"\x00\x01\x02\x03",
            max_bytes=MAX_BYTES,
        )


def test_docx_is_detected_and_extracted() -> None:
    data = _docx_bytes("First procedure", "Second step")
    assert (
        validate_upload(
            filename="guide.docx",
            declared_mime=DOCX_MIME,
            data=data,
            max_bytes=MAX_BYTES,
        )
        == DOCX_MIME
    )
    pages = extract_pages(data, DOCX_MIME, max_pages=10)
    assert len(pages) == 1
    assert pages[0].page_number == 1
    assert "First procedure" in pages[0].text


def test_pdf_page_limit_is_enforced() -> None:
    writer = PdfWriter()
    for _ in range(3):
        writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)

    with pytest.raises(DocumentValidationError, match="page limit"):
        extract_pages(buffer.getvalue(), PDF_MIME, max_pages=2)


def test_pdf_without_text_is_rejected() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buffer = io.BytesIO()
    writer.write(buffer)

    with pytest.raises(DocumentValidationError, match="no extractable text"):
        extract_pages(buffer.getvalue(), PDF_MIME, max_pages=10)


def test_clean_text_normalises_whitespace_and_controls() -> None:
    assert clean_text("  a\r\n\r\n\r\nb\x00  ") == "a\n\nb"


def test_chunks_respect_size_overlap_and_page_numbers() -> None:
    page_one = TextPage(
        page_number=1,
        text=" ".join(f"step{index}" for index in range(80)),
    )
    page_two = TextPage(page_number=2, text="Step 81 for the customer")

    chunks = chunk_pages(
        [page_one, page_two], chunk_tokens=20, overlap_tokens=5
    )

    assert len(chunks) > 3
    assert [chunk.index for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.token_count <= 20 for chunk in chunks)
    assert chunks[0].page_number == 1
    assert chunks[-1].page_number == 2
    assert chunks[-1].content == "Step 81 for the customer"


def test_invalid_chunk_sizing_is_rejected() -> None:
    with pytest.raises(ValueError):
        chunk_pages(
            [TextPage(page_number=1, text="hello")],
            chunk_tokens=10,
            overlap_tokens=10,
        )
