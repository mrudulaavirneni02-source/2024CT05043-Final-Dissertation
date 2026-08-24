from __future__ import annotations

import zipfile
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

import fitz
import pandas as pd

from app.core.errors import AppError


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".xls", ".csv", ".txt", ".md"}
FRIENDLY_ERROR = "Cannot process uploaded file."


def validate_file(path: Path) -> dict:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise AppError(f"Unsupported file type: {suffix}", status_code=415, code="unsupported_file_type")
    if not path.exists() or path.stat().st_size == 0:
        raise AppError(FRIENDLY_ERROR, status_code=422, code="empty_file")

    try:
        if suffix == ".pdf":
            metadata = _validate_pdf(path)
        elif suffix == ".docx":
            metadata = _validate_docx(path)
        elif suffix in {".xlsx", ".xls"}:
            metadata = _validate_excel(path)
        elif suffix == ".csv":
            metadata = _validate_csv(path)
        else:
            metadata = {}
    except AppError:
        raise
    except Exception as exc:
        raise AppError(FRIENDLY_ERROR, status_code=422, code="corrupt_file") from exc

    return {
        "filename": path.name,
        "source": path.name,
        "document_type": suffix.lstrip("."),
        "file_type": suffix.lstrip("."),
        "author": metadata.get("author") or "unknown",
        "creation_date": metadata.get("creation_date") or "",
        "upload_date": datetime.now(UTC).isoformat(),
    }


def _validate_pdf(path: Path) -> dict:
    doc = fitz.open(path)
    try:
        if doc.needs_pass:
            raise AppError(FRIENDLY_ERROR, status_code=422, code="password_protected_pdf")
        if doc.page_count == 0:
            raise AppError(FRIENDLY_ERROR, status_code=422, code="empty_pdf")
        metadata = doc.metadata or {}
        return {
            "author": metadata.get("author") or "unknown",
            "creation_date": _pdf_date(metadata.get("creationDate", "")),
        }
    finally:
        doc.close()


def _validate_docx(path: Path) -> dict:
    if not zipfile.is_zipfile(path):
        raise AppError(FRIENDLY_ERROR, status_code=422, code="broken_docx")
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if "word/document.xml" not in names:
            raise AppError(FRIENDLY_ERROR, status_code=422, code="broken_docx")
        metadata = _docx_core_properties(archive) if "docProps/core.xml" in names else {}
    return metadata


def _validate_excel(path: Path) -> dict:
    workbook = pd.ExcelFile(path)
    if not workbook.sheet_names:
        raise AppError(FRIENDLY_ERROR, status_code=422, code="corrupt_excel")
    return {}


def _validate_csv(path: Path) -> dict:
    pd.read_csv(path, nrows=5)
    return {}


def _docx_core_properties(archive: zipfile.ZipFile) -> dict:
    root = ElementTree.fromstring(archive.read("docProps/core.xml"))
    namespaces = {
        "dc": "http://purl.org/dc/elements/1.1/",
        "dcterms": "http://purl.org/dc/terms/",
    }
    creator = root.findtext("dc:creator", default="", namespaces=namespaces)
    created = root.findtext("dcterms:created", default="", namespaces=namespaces)
    return {"author": creator or "unknown", "creation_date": created or ""}


def _pdf_date(value: str) -> str:
    if not value.startswith("D:") or len(value) < 10:
        return value
    return f"{value[2:6]}-{value[6:8]}-{value[8:10]}"
