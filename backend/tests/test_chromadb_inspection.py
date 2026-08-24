from app.services.retrieval import RetrievalService


class FakeCollection:
    name = "knowledge_chunks"

    def count(self):
        return 2

    def get(self, include):
        return {
            "ids": ["chunk-1", "chunk-2"],
            "documents": ["Chroma stores document vectors.", "BM25 stores lexical retrieval data."],
            "metadatas": [
                {"source": "report.pdf", "file_type": "pdf", "token_count": 5, "page_number": 2},
                {"source": "notes.md", "file_type": "md", "token_count": 6},
            ],
        }


class FakeClient:
    def list_collections(self):
        return [FakeCollection()]


def test_collection_inspection_includes_counts_metadata_and_search_results():
    service = object.__new__(RetrievalService)
    service.write_lock = __import__("threading").RLock()
    service.collection = FakeCollection()
    service.client = FakeClient()

    inspection = service.inspect_collection(query="chroma")

    assert inspection["collections"] == ["knowledge_chunks"]
    assert inspection["vector_count"] == 2
    assert inspection["document_count"] == 2
    assert inspection["total_tokens"] == 11
    assert inspection["records"] == [
        {
            "id": "chunk-1",
            "source": "report.pdf",
            "text": "Chroma stores document vectors.",
            "metadata": {"source": "report.pdf", "file_type": "pdf", "token_count": 5, "page_number": 2},
        }
    ]
