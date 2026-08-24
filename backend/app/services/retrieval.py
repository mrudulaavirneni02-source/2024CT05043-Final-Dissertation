import pickle
from collections import defaultdict
from threading import Event, RLock
from typing import Callable

import chromadb
import numpy as np
from chromadb.config import Settings as ChromaSettings
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer

from app.core.cancellation import raise_if_cancelled
from app.core.config import Settings
from app.models.schemas import RetrievedChunk, RetrievalMode
from app.services.text_processing import Chunk, max_query_coverage, tokenize


RRF_K = 60


class RetrievalService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.write_lock = RLock()
        self.embedding_model = SentenceTransformer(settings.embedding_model)
        self.client = chromadb.PersistentClient(
            path=str(settings.chroma_dir),
            settings=ChromaSettings(anonymized_telemetry=False),
        )
        self.collection = self.client.get_or_create_collection(
            name="knowledge_chunks",
            metadata={"hnsw:space": "cosine"},
        )
        self.bm25_path = settings.bm25_dir / "bm25.pkl"
        self._bm25: BM25Okapi | None = None
        self._bm25_chunks: list[dict] = []
        self._load_bm25()

    def index_chunks(
        self,
        chunks: list[Chunk],
        *,
        cancel_event: Event | None = None,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> int:
        if not chunks:
            return 0
        texts = [chunk.text for chunk in chunks]
        embeddings = self._encode_in_batches(texts, cancel_event=cancel_event, progress=progress, phase="Embedding chunks")
        inserted_ids: list[str] = []
        try:
            for start in range(0, len(chunks), self.settings.embedding_batch_size):
                raise_if_cancelled(cancel_event)
                batch = chunks[start : start + self.settings.embedding_batch_size]
                batch_embeddings = embeddings[start : start + len(batch)].tolist()
                self.collection.upsert(
                    ids=[chunk.id for chunk in batch],
                    documents=[chunk.text for chunk in batch],
                    embeddings=batch_embeddings,
                    metadatas=[chunk.metadata for chunk in batch],
                )
                inserted_ids.extend(chunk.id for chunk in batch)
                if progress:
                    progress("Indexing chunks", min(start + len(batch), len(chunks)), len(chunks))
            self._rebuild_bm25(cancel_event=cancel_event, progress=progress)
        except Exception:
            if inserted_ids:
                self.collection.delete(ids=inserted_ids)
                self._rebuild_bm25()
            raise
        return len(chunks)

    def reset_index(self) -> None:
        """Replace the persisted collection so a changed chunking scheme takes effect."""
        with self.write_lock:
            self.client.delete_collection(name=self.collection.name)
            self.collection = self.client.get_or_create_collection(
                name="knowledge_chunks",
                metadata={"hnsw:space": "cosine"},
            )
            self._bm25 = None
            self._bm25_chunks = []
            if self.bm25_path.exists():
                self.bm25_path.unlink()

    def search(
        self,
        query: str,
        mode: RetrievalMode = "hybrid",
        top_k: int | None = None,
        source: str | None = None,
        *,
        cancel_event: Event | None = None,
    ) -> list[RetrievedChunk]:
        raise_if_cancelled(cancel_event)
        limit = top_k or self.settings.top_k
        if mode == "vector":
            return self._vector_search(query, limit, source=source, cancel_event=cancel_event)
        if mode == "bm25":
            return self._bm25_search(query, limit, source=source, cancel_event=cancel_event)
        return self._hybrid_search(query, limit, source=source, cancel_event=cancel_event)

    def list_sources(self) -> list[dict]:
        data = self.collection.get(include=["metadatas"])
        counts: dict[str, dict] = defaultdict(lambda: {"chunks": 0, "file_type": None, "author": None, "upload_date": None})
        for metadata in data.get("metadatas", []):
            source = metadata.get("source", "unknown")
            counts[source]["chunks"] += 1
            counts[source]["file_type"] = metadata.get("file_type")
            counts[source]["author"] = metadata.get("author")
            counts[source]["upload_date"] = metadata.get("upload_date")
        return [{"source": source, **summary} for source, summary in sorted(counts.items())]

    def stats(self) -> dict:
        sources = self.list_sources()
        return {"documents": len(sources), "chunks": sum(item["chunks"] for item in sources)}

    def inspect_collection(self, query: str = "", limit: int = 100) -> dict:
        """Return a read-only, UI-safe view of the persisted Chroma collection."""
        with self.write_lock:
            data = self.collection.get(include=["documents", "metadatas"])
            collection_names = [_collection_name(item) for item in self.client.list_collections()]

        source_summaries: dict[str, dict] = defaultdict(
            lambda: {"chunks": 0, "file_type": None, "author": None, "upload_date": None, "tokens": 0}
        )
        query = query.strip().lower()
        records: list[dict] = []
        for item_id, text, metadata in zip(
            data.get("ids", []),
            data.get("documents", []),
            data.get("metadatas", []),
            strict=False,
        ):
            metadata = metadata or {}
            source = str(metadata.get("source", "unknown"))
            summary = source_summaries[source]
            summary["chunks"] += 1
            summary["file_type"] = metadata.get("file_type") or summary["file_type"]
            summary["author"] = metadata.get("author") or summary["author"]
            summary["upload_date"] = metadata.get("upload_date") or summary["upload_date"]
            summary["tokens"] += _token_count(metadata, text)
            searchable = f"{item_id}\n{source}\n{text}\n{metadata}".lower()
            if (not query or query in searchable) and len(records) < limit:
                records.append({"id": item_id, "source": source, "text": text, "metadata": metadata})

        documents = [
            {
                "source": source,
                "chunks": summary["chunks"],
                "file_type": summary["file_type"],
                "author": summary["author"],
                "upload_date": summary["upload_date"],
            }
            for source, summary in sorted(source_summaries.items())
        ]
        return {
            "collections": sorted(collection_names),
            "collection_name": self.collection.name,
            "vector_count": self.collection.count(),
            "document_count": len(documents),
            "total_tokens": sum(summary["tokens"] for summary in source_summaries.values()),
            "documents": documents,
            "records": records,
        }

    def confidence(self, results: list[RetrievedChunk], query: str | None = None) -> float:
        if not results:
            return 0.0
        top_score = max(0.0, min(1.0, results[0].score))
        coverage = self.query_coverage(query, results)
        # Rank scores alone are relative to one query.  Subject-token coverage
        # is therefore weighted most heavily when deciding whether to answer.
        confidence = (coverage * 0.8) + (top_score * 0.2)
        return round(max(0.0, min(1.0, confidence)), 4)

    def query_coverage(self, query: str | None, results: list[RetrievedChunk]) -> float:
        return max_query_coverage(query, [item.text for item in results[:3]])

    def _query_overlap_score(self, query: str | None, results: list[RetrievedChunk]) -> float:
        if not query or not results:
            return 0.0
        return self.query_coverage(query, results)

    def _vector_search(self, query: str, limit: int, *, source: str | None = None, cancel_event: Event | None = None) -> list[RetrievedChunk]:
        raise_if_cancelled(cancel_event)
        embedding = self.embedding_model.encode([query], normalize_embeddings=True).tolist()[0]
        raise_if_cancelled(cancel_event)
        query_args = {
            "query_embeddings": [embedding],
            "n_results": limit,
            "include": ["documents", "metadatas", "distances"],
        }
        if source:
            query_args["where"] = {"source": source}
        results = self.collection.query(**query_args)
        chunks: list[RetrievedChunk] = []
        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]
        for item_id, text, metadata, distance in zip(ids, documents, metadatas, distances, strict=False):
            score = max(0.0, 1.0 - float(distance))
            chunks.append(RetrievedChunk(id=item_id, text=text, source=metadata.get("source", "unknown"), score=round(score, 4), metadata=metadata))
        return chunks

    def _bm25_search(self, query: str, limit: int, *, source: str | None = None, cancel_event: Event | None = None) -> list[RetrievedChunk]:
        if self._bm25 is None or not self._bm25_chunks:
            return []
        scores = self._bm25.get_scores(tokenize(query))
        if len(scores) == 0:
            return []
        ranked = np.argsort(scores)[::-1]
        results: list[RetrievedChunk] = []
        for index in ranked:
            raise_if_cancelled(cancel_event)
            raw_score = float(scores[index])
            if raw_score <= 0:
                continue
            item = self._bm25_chunks[int(index)]
            metadata = item["metadata"]
            if source and metadata.get("source") != source:
                continue
            results.append(
                RetrievedChunk(
                    id=item["id"],
                    text=item["text"],
                    source=metadata.get("source", "unknown"),
                    # Raw BM25 values are intentionally kept internal.  They
                    # are not calibrated probabilities and must not be used as
                    # answer-confidence values.
                    score=raw_score,
                    metadata=metadata,
                )
            )
            if len(results) >= limit:
                break
        return results

    def _hybrid_search(self, query: str, limit: int, *, source: str | None = None, cancel_event: Event | None = None) -> list[RetrievedChunk]:
        vector_results = self._vector_search(query, limit * 2, source=source, cancel_event=cancel_event)
        bm25_results = self._bm25_search(query, limit * 2, source=source, cancel_event=cancel_event)
        merged: dict[str, RetrievedChunk] = {}
        scores: dict[str, float] = defaultdict(float)
        for rank, result in enumerate(vector_results, start=1):
            merged[result.id] = result
            scores[result.id] += self.settings.hybrid_vector_weight / (RRF_K + rank)
        for rank, result in enumerate(bm25_results, start=1):
            merged[result.id] = result
            scores[result.id] += self.settings.hybrid_bm25_weight / (RRF_K + rank)
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]
        output = []
        for item_id, score in ranked:
            chunk = merged[item_id]
            output.append(chunk.model_copy(update={"score": round(score, 4)}))
        return output

    def _load_bm25(self) -> None:
        if not self.bm25_path.exists():
            return
        with self.bm25_path.open("rb") as handle:
            payload = pickle.load(handle)
        self._bm25_chunks = payload["chunks"]
        tokenized = [tokenize(item["text"]) for item in self._bm25_chunks]
        self._bm25 = BM25Okapi(tokenized) if tokenized else None

    def _rebuild_bm25(
        self,
        *,
        cancel_event: Event | None = None,
        progress: Callable[[str, int, int], None] | None = None,
    ) -> None:
        data = self.collection.get(include=["documents", "metadatas"])
        self._bm25_chunks = []
        for item_id, text, metadata in zip(data.get("ids", []), data.get("documents", []), data.get("metadatas", []), strict=False):
            self._bm25_chunks.append({"id": item_id, "text": text, "metadata": metadata})
        tokenized = []
        total = len(self._bm25_chunks)
        for index, item in enumerate(self._bm25_chunks, start=1):
            raise_if_cancelled(cancel_event)
            tokenized.append(tokenize(item["text"]))
            if progress and (index == total or index % self.settings.embedding_batch_size == 0):
                progress("Updating BM25 index", index, total)
        self._bm25 = BM25Okapi(tokenized) if tokenized else None
        self.settings.bm25_dir.mkdir(parents=True, exist_ok=True)
        with self.bm25_path.open("wb") as handle:
            pickle.dump({"chunks": self._bm25_chunks}, handle)

    def _encode_in_batches(
        self,
        texts: list[str],
        *,
        cancel_event: Event | None,
        progress: Callable[[str, int, int], None] | None,
        phase: str,
    ) -> np.ndarray:
        batches: list[np.ndarray] = []
        total = len(texts)
        for start in range(0, total, self.settings.embedding_batch_size):
            raise_if_cancelled(cancel_event)
            batch = texts[start : start + self.settings.embedding_batch_size]
            encoded = self.embedding_model.encode(batch, normalize_embeddings=True, show_progress_bar=False)
            batches.append(np.asarray(encoded))
            if progress:
                progress(phase, min(start + len(batch), total), total)
        return np.concatenate(batches, axis=0)


def get_retrieval_service(settings: Settings) -> RetrievalService:
    return RetrievalService(settings)


def _collection_name(collection) -> str:
    return collection.name if hasattr(collection, "name") else str(collection)


def _token_count(metadata: dict, text: str) -> int:
    value = metadata.get("token_count")
    try:
        return int(value)
    except (TypeError, ValueError):
        return len(str(text).split())
