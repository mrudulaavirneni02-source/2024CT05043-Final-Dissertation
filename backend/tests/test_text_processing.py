from app.services.text_processing import chunk_text, clean_text, semantic_chunk_text, tokenize


class FakeEmbeddingModel:
    def encode(self, texts, normalize_embeddings=True):
        return [[1.0, 0.0] if "retrieval" in text.lower() else [0.0, 1.0] for text in texts]


def test_clean_text_removes_extra_spacing():
    assert clean_text("A   B\n\n\nC") == "A B\n\nC"


def test_chunk_text_overlaps():
    chunks = chunk_text(
        " ".join(str(i) for i in range(20)),
        chunk_size=10,
        overlap=2,
        source="unit",
    )
    assert len(chunks) == 3
    assert chunks[0].metadata["chunk_index"] == 0
    assert chunks[0].metadata["chunk_id"] == "unit:0000"
    assert chunks[0].metadata["token_count"] == 10
    assert chunks[1].text.startswith("8 9")


def test_tokenize_lowercases_words():
    assert tokenize("RAG-based BM25!") == ["rag", "based", "bm25"]


def test_semantic_chunk_text_adds_citation_metadata():
    text = "Page 2\nResults\nRetrieval quality improved.\n\nRetrieval latency was measured."
    chunks = semantic_chunk_text(
        text,
        embedding_model=FakeEmbeddingModel(),
        chunk_size=50,
        source="paper.pdf",
        base_metadata={"author": "A", "file_type": "pdf", "document_type": "pdf"},
    )
    assert chunks[0].metadata["page_number"] == "2"
    assert chunks[0].metadata["section"] == "Results"
    assert chunks[0].metadata["chunking_strategy"] == "semantic"
