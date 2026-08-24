import re
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Event
from typing import Any, Callable

import numpy as np

from app.core.cancellation import raise_if_cancelled


@dataclass(frozen=True)
class Chunk:
    id: str
    text: str
    metadata: dict


def clean_text(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-zA-Z0-9]+", text.lower())


# These terms occur in research questions frequently but do not identify the
# subject of the question.  Excluding them prevents a generic paper containing
# "study" or "students" from passing the grounding check.
QUERY_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "between", "by", "did", "do", "does",
    "for", "from", "how", "in", "is", "it", "of", "on", "or", "that", "the", "their",
    "this", "to", "was", "were", "what", "when", "where", "which", "who", "why", "with",
    "according", "actual", "find", "found", "participants", "performance", "question",
    "research", "results", "study", "students", "than", "use", "using",
}


def significant_query_terms(query: str) -> set[str]:
    """Return subject-bearing query tokens for grounding, not retrieval ranking."""
    return {token for token in tokenize(query) if len(token) > 2 and token not in QUERY_STOPWORDS}


def max_query_coverage(query: str | None, texts: list[str]) -> float:
    """Measure whether any retrieved chunk addresses the question's subject."""
    if not query or not texts:
        return 0.0
    query_terms = significant_query_terms(query)
    if not query_terms:
        return 0.0
    return round(max((len(query_terms & set(tokenize(text))) / len(query_terms) for text in texts), default=0.0), 4)


def chunk_text(
    text: str,
    *,
    chunk_size: int,
    overlap: int,
    source: str,
    base_metadata: dict | None = None,
    id_prefix: str | None = None,
    cancel_event: Event | None = None,
) -> list[Chunk]:
    cleaned = clean_text(text)
    if not cleaned:
        return []
    words = cleaned.split()
    chunks: list[Chunk] = []
    step = max(1, chunk_size - overlap)
    metadata = base_metadata or {}
    for start in range(0, len(words), step):
        raise_if_cancelled(cancel_event)
        window = words[start : start + chunk_size]
        if not window:
            continue
        chunk_body = " ".join(window)
        chunk_id = f"{id_prefix or source}:{len(chunks):04d}"
        chunks.append(_make_chunk(chunk_id, chunk_body, source, len(chunks), metadata, word_start=start, strategy="recursive"))
        if start + chunk_size >= len(words):
            break
    return chunks


def semantic_chunk_text(
    text: str,
    *,
    embedding_model: Any,
    chunk_size: int,
    source: str,
    base_metadata: dict | None = None,
    similarity_threshold: float = 0.48,
    minimum_chunk_ratio: float = 0.65,
    embedding_batch_size: int = 32,
    id_prefix: str | None = None,
    cancel_event: Event | None = None,
    progress: Callable[[str, int, int], None] | None = None,
) -> list[Chunk]:
    paragraphs = _extract_paragraph_units(clean_text(text))
    if len(paragraphs) < 2:
        return chunk_text(
            text,
            chunk_size=chunk_size,
            overlap=0,
            source=source,
            base_metadata=base_metadata,
            id_prefix=id_prefix,
            cancel_event=cancel_event,
        )

    paragraph_texts = [item["text"] for item in paragraphs]
    embeddings = _encode_in_batches(
        embedding_model,
        paragraph_texts,
        batch_size=embedding_batch_size,
        cancel_event=cancel_event,
        progress=progress,
    )
    chunks: list[Chunk] = []
    current_texts: list[str] = []
    current_meta: list[dict] = []
    current_words = 0

    for index, item in enumerate(paragraphs):
        raise_if_cancelled(cancel_event)
        words = item["text"].split()
        word_count = len(words)
        related = True
        if current_texts and index > 0:
            related = float(np.dot(embeddings[index - 1], embeddings[index])) >= similarity_threshold
        would_overflow = current_words + word_count > chunk_size and current_texts
        if would_overflow or (current_texts and not related and current_words >= chunk_size * minimum_chunk_ratio):
            chunks.append(_semantic_chunk(source, id_prefix or source, chunks, current_texts, current_meta, base_metadata or {}))
            current_texts = []
            current_meta = []
            current_words = 0

        current_texts.append(item["text"])
        current_meta.append(item)
        current_words += word_count

    if current_texts:
        chunks.append(_semantic_chunk(source, id_prefix or source, chunks, current_texts, current_meta, base_metadata or {}))
    return chunks


def _semantic_chunk(source: str, id_prefix: str, chunks: list[Chunk], texts: list[str], metas: list[dict], base_metadata: dict) -> Chunk:
    chunk_id = f"{id_prefix}:{len(chunks):04d}"
    page_numbers = [item.get("page_number") for item in metas if item.get("page_number")]
    section = next((item.get("section") for item in metas if item.get("section")), "")
    return _make_chunk(
        chunk_id,
        "\n\n".join(texts),
        source,
        len(chunks),
        {**base_metadata, "page_number": page_numbers[0] if page_numbers else "", "section": section},
        strategy="semantic",
    )


def _encode_in_batches(
    embedding_model: Any,
    texts: list[str],
    *,
    batch_size: int,
    cancel_event: Event | None,
    progress: Callable[[str, int, int], None] | None,
) -> np.ndarray:
    batches: list[np.ndarray] = []
    total = len(texts)
    for start in range(0, total, batch_size):
        raise_if_cancelled(cancel_event)
        batch = texts[start : start + batch_size]
        try:
            encoded = embedding_model.encode(batch, normalize_embeddings=True, show_progress_bar=False)
        except TypeError:
            encoded = embedding_model.encode(batch, normalize_embeddings=True)
        batches.append(np.asarray(encoded))
        if progress:
            progress("Finding semantic boundaries", min(start + len(batch), total), total)
    return np.concatenate(batches, axis=0)


def _extract_paragraph_units(text: str) -> list[dict]:
    units: list[dict] = []
    page_number = ""
    section = ""
    for block in re.split(r"\n\s*\n", text):
        block = block.strip()
        if not block:
            continue
        page_match = re.match(r"^Page\s+(\d+)\s*(.*)$", block, flags=re.IGNORECASE | re.DOTALL)
        if page_match:
            page_number = page_match.group(1)
            block = page_match.group(2).strip()
            if not block:
                continue
        first_line = block.splitlines()[0].strip()
        if _looks_like_section(first_line):
            section = first_line
        units.append({"text": block, "page_number": page_number, "section": section})
    return units


def _looks_like_section(line: str) -> bool:
    if len(line) > 90 or len(line.split()) > 12:
        return False
    return bool(re.match(r"^(\d+(\.\d+)*\s+)?[A-Z][A-Za-z0-9 /&,:-]+$", line))


def _make_chunk(
    chunk_id: str,
    text: str,
    source: str,
    chunk_index: int,
    metadata: dict,
    *,
    strategy: str,
    word_start: int | None = None,
) -> Chunk:
    enriched = {
        "filename": metadata.get("filename") or source,
        "source": source,
        "page_number": metadata.get("page_number", ""),
        "section": metadata.get("section", ""),
        "chunk_id": chunk_id,
        "chunk_index": chunk_index,
        "author": metadata.get("author", "unknown"),
        "creation_date": metadata.get("creation_date", ""),
        "upload_date": metadata.get("upload_date") or datetime.now(UTC).isoformat(),
        "document_type": metadata.get("document_type") or metadata.get("file_type", ""),
        "file_type": metadata.get("file_type", ""),
        "chunk_length": len(text),
        "token_count": len(tokenize(text)),
        "chunking_strategy": strategy,
    }
    if word_start is not None:
        enriched["word_start"] = word_start
    sanitized = {key: _metadata_value(value) for key, value in {**metadata, **enriched}.items()}
    return Chunk(id=chunk_id, text=text, metadata=sanitized)


def _metadata_value(value: Any) -> str | int | float | bool:
    if isinstance(value, bool | int | float | str):
        return value
    if value is None:
        return ""
    return str(value)
