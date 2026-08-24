from pathlib import Path

import fitz

from app.services import extraction


def test_pdf_text_survives_a_failed_ocr_page(tmp_path, monkeypatch):
    """An unreadable image page must not discard native text from another page."""
    pdf_path = Path(tmp_path) / "mixed.pdf"
    document = fitz.open()
    text_page = document.new_page()
    text_page.insert_text((72, 72), "Native PDF text is retained.")
    document.new_page()  # Empty page triggers the OCR fallback.
    document.save(pdf_path)
    document.close()

    monkeypatch.setattr(extraction, "_configure_tesseract", lambda command: True)
    monkeypatch.setattr(extraction, "_ocr_pdf_page", lambda page, number, language: "")

    text, metadata = extraction.extract_pdf_text(pdf_path, enable_ocr_fallback=True)

    assert "Native PDF text is retained." in text
    assert metadata["ocr_pages"] == 0
    assert metadata["skipped_image_pages"] == 1


def test_pdf_uses_ocr_for_a_page_without_native_text(tmp_path, monkeypatch):
    pdf_path = Path(tmp_path) / "scan.pdf"
    document = fitz.open()
    document.new_page()
    document.save(pdf_path)
    document.close()

    monkeypatch.setattr(extraction, "_configure_tesseract", lambda command: True)
    monkeypatch.setattr(extraction, "_ocr_pdf_page", lambda page, number, language: "OCR recovered text")

    text, metadata = extraction.extract_pdf_text(pdf_path, enable_ocr_fallback=True)

    assert "OCR recovered text" in text
    assert metadata["ocr_pages"] == 1
