"""Upload validation, text extraction and token-aware chunking.

The content itself decides the format: a PDF must begin with ``%PDF-``, a DOCX
must be a ZIP containing ``word/document.xml``, and anything else must be valid
UTF-8 text or a recognised image. The declared content type is only a hint, so a
mislabelled or malicious upload cannot trick the extractor.

Images and scanned PDFs require an OCR engine. ``extract_pages`` remains the
synchronous text-only path; ``extract_pages_async`` adds the optional OCR
fallback used by the ingestion worker.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass

import tiktoken
from docx import Document as DocxDocument
from pypdf import PdfReader

from app.services.ocr import OCREngine

PDF_MIME = "application/pdf"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TEXT_MIME = "text/plain"
MARKDOWN_MIME = "text/markdown"
PNG_MIME = "image/png"
JPEG_MIME = "image/jpeg"
GIF_MIME = "image/gif"
BMP_MIME = "image/bmp"
TIFF_MIME = "image/tiff"
WEBP_MIME = "image/webp"
IMAGE_MIME_TYPES = frozenset(
    {PNG_MIME, JPEG_MIME, GIF_MIME, BMP_MIME, TIFF_MIME, WEBP_MIME}
)
SUPPORTED_MIME_TYPES = frozenset(
    {PDF_MIME, DOCX_MIME, TEXT_MIME, MARKDOWN_MIME, *IMAGE_MIME_TYPES}
)

SUPPORTED_EXTENSIONS = frozenset(
    {
        ".pdf",
        ".docx",
        ".txt",
        ".md",
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".tif",
        ".tiff",
        ".webp",
    }
)
MAX_DOCX_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_BLANK_LINES = re.compile(r"\n{3,}")

_ENCODING = tiktoken.get_encoding("cl100k_base")


class DocumentValidationError(Exception):
    """A user-correctable problem with an uploaded document."""


class ScannedPdfError(DocumentValidationError):
    """A PDF that contains no embedded text and may need OCR."""


@dataclass(slots=True)
class TextPage:
    page_number: int
    text: str


@dataclass(slots=True)
class ChunkDraft:
    index: int
    content: str
    token_count: int
    page_number: int | None


def extension_of(filename: str) -> str:
    name = filename.strip().lower()
    dot = name.rfind(".")
    return name[dot:] if dot >= 0 else ""


def _detect_image_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG_MIME
    if data.startswith(b"\xff\xd8\xff"):
        return JPEG_MIME
    if data.startswith((b"GIF87a", b"GIF89a")):
        return GIF_MIME
    if data.startswith(b"BM"):
        return BMP_MIME
    if data.startswith((b"II*\x00", b"MM\x00*")):
        return TIFF_MIME
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return WEBP_MIME
    return None


def _detect_mime(data: bytes, *, max_uncompressed: int) -> str:
    if data.startswith(b"%PDF-"):
        return PDF_MIME

    image_mime = _detect_image_mime(data)
    if image_mime is not None:
        return image_mime

    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
                if "word/document.xml" not in names:
                    raise DocumentValidationError(
                        "The uploaded archive is not a Word document."
                    )
                total = sum(info.file_size for info in archive.infolist())
                if total > max_uncompressed:
                    raise DocumentValidationError(
                        "The Word document expands beyond the allowed size."
                    )
                return DOCX_MIME
        except zipfile.BadZipFile as exc:
            raise DocumentValidationError("The uploaded file is damaged.") from exc

    if b"\x00" in data[:8192]:
        raise DocumentValidationError(
            "Only PDF, Word (.docx), text, Markdown and image files are supported."
        )
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentValidationError(
            "Text files must be UTF-8 encoded."
        ) from exc
    return TEXT_MIME


def validate_upload(
    *,
    filename: str,
    declared_mime: str | None,
    data: bytes,
    max_bytes: int,
    images_supported: bool = False,
) -> str:
    """Return the detected MIME type or raise a user-safe validation error."""
    if not data:
        raise DocumentValidationError("The uploaded file is empty.")
    if len(data) > max_bytes:
        raise DocumentValidationError(
            f"The file is larger than the {max_bytes // (1024 * 1024)} MB limit."
        )

    extension = extension_of(filename)
    if extension and extension not in SUPPORTED_EXTENSIONS:
        raise DocumentValidationError(
            "Unsupported file type. Upload a PDF, Word (.docx), text, Markdown "
            "or image file."
        )

    detected = _detect_mime(data, max_uncompressed=MAX_DOCX_UNCOMPRESSED_BYTES)
    if detected in IMAGE_MIME_TYPES and not images_supported:
        raise DocumentValidationError(
            "Image documents need OCR, which is not enabled on this deployment."
        )

    declared = (declared_mime or "").split(";")[0].strip().lower()
    if declared and declared not in {*SUPPORTED_MIME_TYPES, "application/octet-stream"}:
        raise DocumentValidationError("That content type is not supported.")

    expected_by_extension = {
        ".pdf": PDF_MIME,
        ".docx": DOCX_MIME,
        ".txt": TEXT_MIME,
        ".md": MARKDOWN_MIME,
        ".png": PNG_MIME,
        ".jpg": JPEG_MIME,
        ".jpeg": JPEG_MIME,
        ".gif": GIF_MIME,
        ".bmp": BMP_MIME,
        ".tif": TIFF_MIME,
        ".tiff": TIFF_MIME,
        ".webp": WEBP_MIME,
    }
    if extension in expected_by_extension:
        expected = expected_by_extension[extension]
        text_equivalent = detected == TEXT_MIME and expected in {
            TEXT_MIME,
            MARKDOWN_MIME,
        }
        if detected != expected and not text_equivalent:
            raise DocumentValidationError(
                "The file content does not match its extension."
            )

    return detected


def clean_text(text: str) -> str:
    normalised = text.replace("\r\n", "\n").replace("\r", "\n")
    normalised = _CONTROL_CHARS.sub("", normalised)
    lines = [line.strip() for line in normalised.split("\n")]
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _extract_pdf(data: bytes, max_pages: int) -> list[TextPage]:
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            try:
                reader.decrypt("")
            except Exception as exc:  # noqa: BLE001 - pypdf raises varied errors
                raise DocumentValidationError(
                    "Password-protected PDFs are not supported."
                ) from exc
        if len(reader.pages) > max_pages:
            raise DocumentValidationError(
                f"The PDF has more than the {max_pages}-page limit."
            )
        pages: list[TextPage] = []
        for number, page in enumerate(reader.pages, start=1):
            try:
                text = clean_text(page.extract_text() or "")
            except Exception as exc:  # noqa: BLE001 - damaged pages vary
                raise DocumentValidationError(
                    f"Page {number} could not be read."
                ) from exc
            if text:
                pages.append(TextPage(page_number=number, text=text))
    except DocumentValidationError:
        raise
    except Exception as exc:  # noqa: BLE001 - pypdf raises varied errors
        raise DocumentValidationError("The PDF could not be read.") from exc

    if not pages:
        raise ScannedPdfError(
            "The PDF contains no extractable text. Scanned documents need OCR."
        )
    return pages


def _extract_docx(data: bytes, max_pages: int) -> list[TextPage]:
    try:
        document = DocxDocument(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 - python-docx raises varied errors
        raise DocumentValidationError("The Word document could not be read.") from exc

    blocks: list[str] = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            blocks.append(" | ".join(cell.text for cell in row.cells))

    text = clean_text("\n".join(blocks))
    if not text:
        raise DocumentValidationError("The Word document contains no text.")
    # Word has no reliable physical page count without rendering; one logical
    # page keeps citations honest rather than inventing page numbers.
    return [TextPage(page_number=1, text=text)]


def _extract_text(data: bytes, max_pages: int) -> list[TextPage]:
    text = clean_text(data.decode("utf-8-sig"))
    if not text:
        raise DocumentValidationError("The text file is empty.")
    return [TextPage(page_number=1, text=text)]


def extract_pages(data: bytes, mime_type: str, *, max_pages: int) -> list[TextPage]:
    """Synchronous extraction for formats with embedded text."""
    if mime_type == PDF_MIME:
        return _extract_pdf(data, max_pages)
    if mime_type == DOCX_MIME:
        return _extract_docx(data, max_pages)
    if mime_type in IMAGE_MIME_TYPES:
        raise DocumentValidationError(
            "Image documents need OCR, which is not enabled on this deployment."
        )
    return _extract_text(data, max_pages)


async def extract_pages_async(
    data: bytes,
    mime_type: str,
    *,
    max_pages: int,
    ocr: OCREngine | None = None,
) -> list[TextPage]:
    """Extract text, falling back to OCR for images and scanned PDFs.

    The OCR engine is optional so the default installation keeps a small
    dependency set and fails with a clear, user-facing message instead of
    silently accepting a document it cannot read.
    """
    if mime_type in IMAGE_MIME_TYPES:
        if ocr is None:
            raise DocumentValidationError(
                "Image documents need OCR, which is not enabled on this deployment."
            )
        try:
            text = clean_text(await ocr.extract_image(data, mime_type=mime_type))
        except DocumentValidationError:
            raise
        except Exception as exc:  # noqa: BLE001 - engine errors become user-safe
            raise DocumentValidationError(
                "OCR could not read the image document."
            ) from exc
        if not text:
            raise DocumentValidationError("OCR found no readable text in the image.")
        return [TextPage(page_number=1, text=text)]

    if mime_type == PDF_MIME:
        try:
            return _extract_pdf(data, max_pages)
        except ScannedPdfError:
            if ocr is None:
                raise DocumentValidationError(
                    "The PDF contains no extractable text. Enable OCR to ingest "
                    "scanned documents."
                ) from None
            try:
                raw_pages = await ocr.extract_pdf(data, max_pages=max_pages)
            except Exception as exc:  # noqa: BLE001 - engine errors become user-safe
                raise DocumentValidationError(
                    "OCR could not read the scanned PDF."
                ) from exc
            pages: list[TextPage] = []
            for number, raw_text in raw_pages:
                cleaned = clean_text(raw_text)
                if cleaned:
                    pages.append(TextPage(page_number=number, text=cleaned))
            if not pages:
                raise DocumentValidationError(
                    "OCR found no readable text in the scanned PDF."
                ) from None
            return pages

    return extract_pages(data, mime_type, max_pages=max_pages)


def estimate_tokens(text: str) -> int:
    return len(_ENCODING.encode(text))


def chunk_pages(
    pages: list[TextPage],
    *,
    chunk_tokens: int,
    overlap_tokens: int,
) -> list[ChunkDraft]:
    if chunk_tokens <= 0 or overlap_tokens < 0 or overlap_tokens >= chunk_tokens:
        raise ValueError("Invalid chunk sizing")

    chunks: list[ChunkDraft] = []
    index = 0
    step = chunk_tokens - overlap_tokens

    for page in pages:
        text = clean_text(page.text)
        if not text:
            continue
        tokens = _ENCODING.encode(text)
        if len(tokens) <= chunk_tokens:
            chunks.append(
                ChunkDraft(
                    index=index,
                    content=text,
                    token_count=len(tokens),
                    page_number=page.page_number,
                )
            )
            index += 1
            continue

        start = 0
        while start < len(tokens):
            window = tokens[start : start + chunk_tokens]
            content = _ENCODING.decode(window).strip()
            if content:
                chunks.append(
                    ChunkDraft(
                        index=index,
                        content=content,
                        token_count=len(window),
                        page_number=page.page_number,
                    )
                )
                index += 1
            if start + chunk_tokens >= len(tokens):
                break
            start += step

    if not chunks:
        raise DocumentValidationError("The document produced no usable text.")
    return chunks
