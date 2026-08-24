from pathlib import Path
from threading import Event
from typing import Callable
from xml.etree import ElementTree
import zipfile

import fitz
import pandas as pd
import pytesseract
from PIL import Image
from loguru import logger

from app.core.cancellation import raise_if_cancelled
from app.core.errors import AppError
from app.services.text_processing import clean_text
from app.validation.corruption_checker import SUPPORTED_EXTENSIONS


def extract_file_text(
    path: Path,
    base_metadata: dict | None = None,
    *,
    enable_ocr_fallback: bool = False,
    tesseract_cmd: str = "",
    ocr_language: str = "eng",
    cancel_event: Event | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> tuple[str, dict]:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise AppError(f"Unsupported file type: {suffix}", status_code=415, code="unsupported_file_type")
    metadata = dict(base_metadata or {})
    if suffix == ".pdf":
        text, pdf_metadata = extract_pdf_text(
            path,
            enable_ocr_fallback=enable_ocr_fallback,
            tesseract_cmd=tesseract_cmd,
            ocr_language=ocr_language,
            cancel_event=cancel_event,
            progress=progress,
        )
        return text, {**metadata, "file_type": "pdf", "document_type": "pdf", **pdf_metadata}
    if suffix == ".docx":
        return extract_docx_text(path), {**metadata, "file_type": "docx", "document_type": "docx"}
    if suffix in {".xlsx", ".xls"}:
        return extract_excel_text(path), {**metadata, "file_type": "excel", "document_type": "excel"}
    if suffix == ".csv":
        return extract_csv_text(path), {**metadata, "file_type": "csv", "document_type": "csv"}
    doc_type = suffix.lstrip(".")
    return clean_text(path.read_text(encoding="utf-8", errors="ignore")), {**metadata, "file_type": doc_type, "document_type": doc_type}


def extract_pdf_text(
    path: Path,
    *,
    enable_ocr_fallback: bool = False,
    tesseract_cmd: str = "",
    ocr_language: str = "eng",
    cancel_event: Event | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> tuple[str, dict]:
    """Extract native PDF text and OCR only pages without readable text.

    Image decoding and OCR are intentionally isolated per page: an unsupported,
    damaged, or non-text image must never prevent extraction of other pages or
    native text present in the same uploaded PDF.
    """
    ocr_available = enable_ocr_fallback and _configure_tesseract(tesseract_cmd)
    doc = fitz.open(path)
    pages: list[str] = []
    ocr_pages = 0
    skipped_pages = 0
    try:
        for page_number, page in enumerate(doc, start=1):
            raise_if_cancelled(cancel_event)
            try:
                text = clean_text(page.get_text("text"))
            except Exception as exc:
                logger.warning("Skipping native text extraction on PDF page {}: {}", page_number, exc)
                text = ""

            if not text and ocr_available:
                text = _ocr_pdf_page(page, page_number, ocr_language)
                if text:
                    ocr_pages += 1
                else:
                    skipped_pages += 1
            if text:
                pages.append(f"Page {page_number}\n{text}")
            if progress:
                progress("Extracting text", page_number, doc.page_count)
        return clean_text("\n\n".join(pages)), {
            "ocr_pages": ocr_pages,
            "skipped_image_pages": skipped_pages,
            "ocr_enabled": ocr_available,
        }
    finally:
        doc.close()


def _configure_tesseract(tesseract_cmd: str) -> bool:
    """Configure Tesseract when available without turning missing OCR into a failure."""
    if tesseract_cmd:
        executable = Path(tesseract_cmd)
        if not executable.is_file():
            logger.warning("Tesseract was enabled but not found at {}; OCR fallback will be skipped.", executable)
            return False
        pytesseract.pytesseract.tesseract_cmd = str(executable)
    try:
        pytesseract.get_tesseract_version()
        return True
    except Exception as exc:
        logger.warning("Tesseract is unavailable; OCR fallback will be skipped: {}", exc)
        return False


def _ocr_pdf_page(page: fitz.Page, page_number: int, language: str) -> str:
    try:
        pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
        image = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
        return clean_text(pytesseract.image_to_string(image, lang=language, config="--psm 3"))
    except Exception as exc:
        # A damaged or non-text image is not an ingestion error.  The caller
        # keeps the text collected from every other page.
        logger.warning("Skipping OCR on PDF page {}: {}", page_number, exc)
        return ""


def extract_docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        xml = archive.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    paragraphs: list[str] = []
    for paragraph in root.findall(".//w:p", namespace):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace))
        if text.strip():
            paragraphs.append(text.strip())
    return clean_text("\n\n".join(paragraphs))


def extract_excel_text(path: Path) -> str:
    workbook = pd.read_excel(path, sheet_name=None, dtype=str)
    sections: list[str] = []
    for sheet_name, frame in workbook.items():
        frame = frame.fillna("")
        if frame.empty:
            continue
        rows = []
        for row_index, row in frame.iterrows():
            values = [str(value).strip() for value in row.to_list() if str(value).strip()]
            if values:
                rows.append(f"Row {row_index + 2}: " + " | ".join(values))
        if rows:
            sections.append(f"Sheet: {sheet_name}\n" + "\n".join(rows))
    return clean_text("\n\n".join(sections))


def extract_csv_text(path: Path) -> str:
    frame = pd.read_csv(path, dtype=str).fillna("")
    rows = []
    for row_index, row in frame.iterrows():
        values = [str(value).strip() for value in row.to_list() if str(value).strip()]
        if values:
            rows.append(f"Row {row_index + 2}: " + " | ".join(values))
    return clean_text("\n".join(rows))
