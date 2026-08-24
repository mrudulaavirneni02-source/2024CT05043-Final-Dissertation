from typing import Literal

from pydantic import BaseModel, Field


RetrievalMode = Literal["hybrid", "vector", "bm25"]
ChunkingMode = Literal["recursive", "semantic"]


class HealthResponse(BaseModel):
    status: str
    app_name: str
    environment: str
    embedding_model: str
    ollama_model: str
    storage_ready: bool
    ragas_available: bool
    ragas_version: str | None = None


class DocumentSummary(BaseModel):
    source: str
    chunks: int
    file_type: str | None = None
    author: str | None = None
    upload_date: str | None = None
    duplicate: bool = False


class ChromaRecord(BaseModel):
    id: str
    source: str
    text: str
    metadata: dict


class ChromaInspectionResponse(BaseModel):
    collections: list[str]
    collection_name: str
    vector_count: int
    document_count: int
    total_tokens: int
    documents: list[DocumentSummary]
    records: list[ChromaRecord]


class UploadResponse(BaseModel):
    filename: str
    chunks_indexed: int
    message: str
    sha256: str | None = None
    validation_status: str = "accepted"
    duplicate: bool = False


class IngestionJobResponse(BaseModel):
    id: str
    kind: str
    status: str
    phase: str
    completed: int = 0
    total: int = 0
    result: UploadResponse | None = None
    error: dict[str, str] | None = None
    created_at: str
    finished_at: str | None = None


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=2000)
    mode: RetrievalMode = "hybrid"
    top_k: int | None = Field(default=None, ge=1, le=20)
    source: str | None = Field(default=None, max_length=500)


class RetrievedChunk(BaseModel):
    id: str
    text: str
    source: str
    score: float
    metadata: dict


class SearchResponse(BaseModel):
    query: str
    mode: RetrievalMode
    results: list[RetrievedChunk]
    confidence: float
    latency_ms: float


class ChatRequest(SearchRequest):
    temperature: float = Field(default=0.2, ge=0, le=1)


class ChatResponse(BaseModel):
    answer: str
    citations: list[RetrievedChunk]
    model: str
    retrieval_mode: RetrievalMode
    confidence: float
    retrieval_latency_ms: float
    generation_latency_ms: float
    sources: list[dict]
    grounded: bool


class EvaluationRequest(BaseModel):
    questions: list[str] = Field(min_length=1, max_length=20)
    mode: RetrievalMode = "hybrid"
    relevant_sources: dict[str, list[str]] | None = None
    reference_answers: dict[str, str] | None = None
    source: str | None = Field(default=None, max_length=500)
    include_ragas: bool = True


class EvaluationItem(BaseModel):
    question: str
    retrieved: int
    top_score: float
    average_similarity: float
    context_available: bool
    precision_at_k: float
    recall_at_k: float
    mrr: float
    hit_rate: float
    retrieval_latency_ms: float
    faithfulness: float | None = None
    answer_correctness: float | None = None
    context_precision: float | None = None
    context_recall: float | None = None
    ragas_error: str | None = None


class EvaluationResponse(BaseModel):
    items: list[EvaluationItem]
    average_top_score: float
    average_precision: float
    average_recall: float
    average_mrr: float
    hit_rate: float
    average_latency_ms: float
    average_faithfulness: float | None = None
    average_answer_correctness: float | None = None
    average_context_precision: float | None = None
    average_context_recall: float | None = None
    ragas_completed: bool = False
    ragas_error: str | None = None


class AnalyticsResponse(BaseModel):
    documents: int
    chunks: int
    queries: int
    duplicate_files: int
    rejected_files: int
    average_retrieval_latency_ms: float
    average_generation_latency_ms: float
    average_confidence: float
    embedding_model: str
    retrievers: list[str]
    llm: str
    events_by_type: dict[str, int]
