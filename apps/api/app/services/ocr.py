"""Optional OCR engine for scanned PDFs and image documents.

OCR is deliberately pluggable. Development and CI run with no native OCR
dependencies, so the default engine is ``none`` and image/scanned uploads are
rejected honestly. A deployment that installs Tesseract (and optionally
``pypdfium2`` for PDF rendering) sets ``OCR_ENGINE=tesseract``; no network
service is contacted and no provider key is involved.

The ingestion pipeline accepts any object implementing ``OCREngine``, which is
what lets tests inject a deterministic fake engine without native packages.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import shutil
from collections.abc import Sequence
from typing import Protocol

import structlog

from app.core.config import Settings, get_settings

logger = structlog.get_logger(__name__)


class OCRError(Exception):
    """Raised when an OCR engine cannot process a document."""


class OCREngine(Protocol):
    name: str

    async def extract_image(self, data: bytes, *, mime_type: str) -> str: ...

    async def extract_pdf(
        self, data: bytes, *, max_pages: int
    ) -> list[tuple[int, str]]: ...


def tesseract_available() -> bool:
    if importlib.util.find_spec("PIL") is None:
        return False
    if importlib.util.find_spec("pytesseract") is None:
        return False
    return shutil.which("tesseract") is not None


class TesseractOCREngine:
    """Local OCR through the Tesseract CLI and optional PDF rendering."""

    name = "tesseract"

    def __init__(
        self,
        *,
        language: str,
        timeout_seconds: int,
        max_image_pixels: int,
        pdf_dpi: int,
    ) -> None:
        self.language = language
        self.timeout_seconds = timeout_seconds
        self.max_image_pixels = max_image_pixels
        self.pdf_dpi = pdf_dpi

    def _extract_sync(self, image: object) -> str:
        import pytesseract  # imported lazily: optional dependency

        return pytesseract.image_to_string(
            image,
            lang=self.language,
            timeout=self.timeout_seconds,
        )

    async def extract_image(self, data: bytes, *, mime_type: str) -> str:
        from PIL import Image, UnidentifiedImageError

        def _run() -> str:
            try:
                with Image.open(io.BytesIO(data)) as image:
                    image.load()
                    if image.width * image.height > self.max_image_pixels:
                        raise OCRError(
                            "The image is too large to process safely."
                        )
                    return self._extract_sync(image)
            except OCRError:
                raise
            except UnidentifiedImageError as exc:
                raise OCRError("The image could not be decoded.") from exc
            except Exception as exc:  # noqa: BLE001 - native OCR library varies
                raise OCRError("OCR could not read the image.") from exc

        return await asyncio.to_thread(_run)

    async def extract_pdf(
        self, data: bytes, *, max_pages: int
    ) -> list[tuple[int, str]]:
        if importlib.util.find_spec("pypdfium2") is None:
            raise OCRError(
                "Scanned PDF OCR needs the optional pypdfium2 package."
            )

        def _run() -> list[tuple[int, str]]:
            import pypdfium2 as pdfium

            try:
                document = pdfium.PdfDocument(data)
            except Exception as exc:  # noqa: BLE001 - renderer raises varied errors
                raise OCRError("The PDF could not be opened for OCR.") from exc

            pages: list[tuple[int, str]] = []
            try:
                if len(document) > max_pages:
                    raise OCRError(
                        f"The PDF has more than the {max_pages}-page limit."
                    )
                scale = max(self.pdf_dpi, 72) / 72.0
                for index in range(len(document)):
                    page = document[index]
                    bitmap = page.render(scale=scale)
                    image = bitmap.to_pil()
                    text = self._extract_sync(image)
                    if text.strip():
                        pages.append((index + 1, text))
            finally:
                document.close()
            return pages

        return await asyncio.to_thread(_run)


def build_ocr_engine(settings: Settings | None = None) -> OCREngine | None:
    settings = settings or get_settings()
    if settings.ocr_engine == "none":
        return None
    if settings.ocr_engine == "tesseract":
        if not tesseract_available():
            logger.warning(
                "ocr_engine_unavailable",
                requested="tesseract",
                reason="Pillow, pytesseract or the tesseract binary is missing",
            )
            return None
        return TesseractOCREngine(
            language=settings.ocr_language,
            timeout_seconds=settings.ocr_timeout_seconds,
            max_image_pixels=settings.ocr_max_image_pixels,
            pdf_dpi=settings.ocr_pdf_dpi,
        )
    logger.warning("ocr_engine_unknown", requested=settings.ocr_engine)
    return None


def get_ocr_engine() -> OCREngine | None:
    """FastAPI dependency. Tests override this with a deterministic engine."""
    return build_ocr_engine()


def non_empty_pages(
    pages: Sequence[tuple[int, str]],
) -> list[tuple[int, str]]:
    return [(number, text) for number, text in pages if text.strip()]
