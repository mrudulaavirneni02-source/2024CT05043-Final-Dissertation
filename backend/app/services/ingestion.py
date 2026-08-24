from pathlib import Path
from shutil import copy2
from dataclasses import dataclass
from threading import Event
from typing import Callable

from loguru import logger

from app.core.config import Settings
from app.core.cancellation import raise_if_cancelled
from app.core.errors import AppError
from app.services.extraction import extract_file_text
from app.services.retrieval import RetrievalService
from app.services.text_processing import chunk_text, semantic_chunk_text
from app.validation.corruption_checker import validate_file
from app.validation.duplicate_detector import DuplicateDetector


@dataclass(frozen=True)
class IngestionResult:
    filename: str
    chunks_indexed: int
    message: str
    sha256: str | None = None
    validation_status: str = "accepted"
    duplicate: bool = False


class IngestionService:
    def __init__(self, settings: Settings, retrieval: RetrievalService) -> None:
        self.settings = settings
        self.retrieval = retrieval

    def ingest_seed(
        self,
        *,
        cancel_event: Event | None = None,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> IngestionResult:
        raise_if_cancelled(cancel_event)
        seed_path = self.settings.data_dir / "seed" / "2024CT05043_abstract_clean.txt"
        if not seed_path.exists():
            raise AppError("Seed transcript not found", status_code=404, code="seed_missing")
        text = seed_path.read_text(encoding="utf-8")
        base_metadata = {
            "filename": "2024CT05043_abstract_clean.txt",
            "file_type": "seed",
            "document_type": "seed",
            "topic": "2024CT05043 dissertation RAG framework",
            "author": "unknown",
        }
        chunks = semantic_chunk_text(
            text,
            chunk_size=self.settings.chunk_size,
            source="2024CT05043_abstract_clean.txt",
            base_metadata=base_metadata,
            embedding_model=self.retrieval.embedding_model,
            minimum_chunk_ratio=self.settings.semantic_min_chunk_ratio,
            embedding_batch_size=self.settings.embedding_batch_size,
            cancel_event=cancel_event,
            progress=progress,
        )
        if not chunks:
            chunks = chunk_text(
                text,
                chunk_size=self.settings.chunk_size,
                overlap=self.settings.chunk_overlap,
                source="2024CT05043_abstract_clean.txt",
                base_metadata=base_metadata,
                cancel_event=cancel_event,
            )
        indexed = self.retrieval.index_chunks(chunks, cancel_event=cancel_event, progress=progress)
        logger.info("Indexed {} seed chunks", indexed)
        return IngestionResult(filename="2024CT05043_abstract_clean.txt", chunks_indexed=indexed, message="Seed transcript indexed")

    def ingest_upload(
        self,
        path: Path,
        original_filename: str,
        *,
        cancel_event: Event | None = None,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> IngestionResult:
        detector = DuplicateDetector(self.settings)
        with self.retrieval.write_lock:
            raise_if_cancelled(cancel_event)
            if progress:
                progress("Hashing file", 0, 1)
            file_hash = detector.sha256(path, cancel_event=cancel_event)
            if progress:
                progress("Validating file", 1, 1)
            if detector.already_indexed(file_hash):
                raise AppError("Document already indexed.", status_code=409, code="duplicate_document")

            metadata = validate_file(path)
            text, metadata = extract_file_text(
                path,
                metadata,
                enable_ocr_fallback=self.settings.enable_ocr_fallback,
                tesseract_cmd=self.settings.tesseract_cmd,
                ocr_language=self.settings.ocr_language,
                cancel_event=cancel_event,
                progress=progress,
            )
            if not text:
                raise AppError(
                    "No readable text was found. This PDF may need clearer scans or OCR language support.",
                    status_code=422,
                    code="empty_extraction",
                )
            chunks = semantic_chunk_text(
                text,
                chunk_size=self.settings.chunk_size,
                source=original_filename,
                id_prefix=file_hash,
                base_metadata={**metadata, "filename": original_filename, "sha256": file_hash},
                embedding_model=self.retrieval.embedding_model,
                minimum_chunk_ratio=self.settings.semantic_min_chunk_ratio,
                embedding_batch_size=self.settings.embedding_batch_size,
                cancel_event=cancel_event,
                progress=progress,
            )
            indexed = self.retrieval.index_chunks(chunks, cancel_event=cancel_event, progress=progress)
            raise_if_cancelled(cancel_event)
            stored_path = self.settings.upload_dir / f"{file_hash}{path.suffix.lower()}"
            if path.resolve() != stored_path.resolve():
                copy2(path, stored_path)
            detector.register(file_hash, original_filename, indexed)
            logger.info("Indexed {} chunks from {}", indexed, original_filename)
            return IngestionResult(
                filename=original_filename,
                chunks_indexed=indexed,
                message="Document indexed",
                sha256=file_hash,
                validation_status="accepted",
            )

    def reindex_library(
        self,
        *,
        cancel_event: Event | None = None,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> IngestionResult:
        """Rebuild from retained uploads after a retrieval configuration change."""
        detector = DuplicateDetector(self.settings)
        registered = detector.load()
        retained: list[tuple[Path, str, str]] = []
        for file_hash, record in registered.items():
            original_name = str(record.get("filename") or "upload")
            matches = list(self.settings.upload_dir.glob(f"{file_hash}.*"))
            legacy_path = self.settings.upload_dir / original_name
            path = matches[0] if matches else legacy_path
            if path.exists():
                retained.append((path, original_name, file_hash))

        total = len(retained) + 1  # retained uploads plus the seed document
        completed = 0
        with self.retrieval.write_lock:
            self.retrieval.reset_index()
            if progress:
                progress("Re-indexing seed document", completed, total)
            seed_result = self.ingest_seed(cancel_event=cancel_event, progress=None)
            completed += 1
            total_chunks = seed_result.chunks_indexed
            for path, original_name, file_hash in retained:
                raise_if_cancelled(cancel_event)
                if progress:
                    progress(f"Re-indexing {original_name}", completed, total)
                metadata = validate_file(path)
                text, metadata = extract_file_text(
                    path,
                    metadata,
                    enable_ocr_fallback=self.settings.enable_ocr_fallback,
                    tesseract_cmd=self.settings.tesseract_cmd,
                    ocr_language=self.settings.ocr_language,
                    cancel_event=cancel_event,
                    progress=None,
                )
                if not text:
                    logger.warning("Skipped {} during re-indexing because no text was extracted", original_name)
                    completed += 1
                    continue
                chunks = semantic_chunk_text(
                    text,
                    chunk_size=self.settings.chunk_size,
                    source=original_name,
                    id_prefix=file_hash,
                    base_metadata={**metadata, "filename": original_name, "sha256": file_hash},
                    embedding_model=self.retrieval.embedding_model,
                    minimum_chunk_ratio=self.settings.semantic_min_chunk_ratio,
                    embedding_batch_size=self.settings.embedding_batch_size,
                    cancel_event=cancel_event,
                    progress=None,
                )
                total_chunks += self.retrieval.index_chunks(chunks, cancel_event=cancel_event, progress=None)
                detector.register(file_hash, original_name, len(chunks))
                completed += 1
            if progress:
                progress("Re-index complete", total, total)
        return IngestionResult(
            filename="Indexed document library",
            chunks_indexed=total_chunks,
            message="Library re-indexed with evidence-sized chunks",
        )
