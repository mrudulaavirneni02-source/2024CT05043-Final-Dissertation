import asyncio
import csv
import io
from pathlib import Path
from threading import Lock
from time import perf_counter
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Header, Request, UploadFile
from fastapi.responses import Response

from app.core.config import Settings, get_settings
from app.core.cancellation import operations
from app.core.errors import AppError
from app.models.schemas import (
    AnalyticsResponse,
    ChromaInspectionResponse,
    ChatRequest,
    ChatResponse,
    DocumentSummary,
    EvaluationRequest,
    EvaluationResponse,
    HealthResponse,
    IngestionJobResponse,
    RetrievedChunk,
    SearchRequest,
    SearchResponse,
)
from app.services.analytics import AnalyticsService
from app.services.evaluation import EvaluationService, RAGAS_AVAILABLE, RAGAS_VERSION
from app.services.ingestion import IngestionService
from app.services.jobs import ingestion_jobs
from app.services.llm import OllamaClient
from app.services.retrieval import RetrievalService
from app.services.text_processing import max_query_coverage
from app.validation.corruption_checker import SUPPORTED_EXTENSIONS

router = APIRouter()
_retrieval_service: RetrievalService | None = None
_retrieval_lock = Lock()


def _query_overlap_score(query: str | None, results: list[RetrievedChunk]) -> float:
    if not query or not results:
        return 0.0
    return max_query_coverage(query, [item.text for item in results[:3]])


def should_ground_context(
    confidence: float,
    results: list[RetrievedChunk],
    *,
    query: str | None = None,
    min_context_score: float = 0.15,
) -> bool:
    if not results:
        return False
    overlap_score = _query_overlap_score(query, results)
    # Both requirements are necessary: a rank can be high even if every
    # indexed document is irrelevant, while token overlap alone can be noisy.
    return confidence >= min_context_score and overlap_score >= 0.5


def get_retrieval(settings: Settings = Depends(get_settings)) -> RetrievalService:
    global _retrieval_service
    with _retrieval_lock:
        if _retrieval_service is None:
            _retrieval_service = RetrievalService(settings)
    return _retrieval_service


@router.get("/health", response_model=HealthResponse)
def health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    storage_ready = all(path.exists() for path in [settings.data_dir, settings.upload_dir, settings.chroma_dir, settings.bm25_dir])
    return HealthResponse(
        status="ok",
        app_name=settings.app_name,
        environment=settings.app_env,
        embedding_model=settings.embedding_model,
        ollama_model=settings.ollama_model,
        storage_ready=storage_ready,
        ragas_available=RAGAS_AVAILABLE,
        ragas_version=RAGAS_VERSION,
    )


@router.post("/api/documents/ingest-seed", response_model=IngestionJobResponse, status_code=202)
async def ingest_seed(settings: Settings = Depends(get_settings)) -> dict:
    def work(cancel_event, progress):
        result = IngestionService(settings, get_retrieval(settings)).ingest_seed(cancel_event=cancel_event, progress=progress)
        AnalyticsService(settings).log_event("upload", filename=result.filename, chunks_indexed=result.chunks_indexed, source="seed")
        return result.__dict__

    return ingestion_jobs.submit("seed", work).snapshot()


@router.post("/api/documents/reindex", response_model=IngestionJobResponse, status_code=202)
async def reindex_library(settings: Settings = Depends(get_settings)) -> dict:
    def work(cancel_event, progress):
        result = IngestionService(settings, get_retrieval(settings)).reindex_library(
            cancel_event=cancel_event,
            progress=progress,
        )
        AnalyticsService(settings).log_event("reindex", chunks_indexed=result.chunks_indexed)
        return result.__dict__

    return ingestion_jobs.submit("reindex", work).snapshot()


@router.post("/api/documents/upload", response_model=IngestionJobResponse, status_code=202)
async def upload_document(
    request: Request,
    file: UploadFile = File(...),
    settings: Settings = Depends(get_settings),
) -> dict:
    safe_name = Path(file.filename or "upload").name
    suffix = Path(safe_name).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise AppError(f"Unsupported file type: {suffix or 'unknown'}", status_code=415, code="unsupported_file_type")
    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    content_length = request.headers.get("content-length") if request else None
    if content_length and int(content_length) > max_bytes:
        raise AppError(f"Files must be {settings.max_upload_size_mb} MB or smaller.", status_code=413, code="file_too_large")
    incoming_dir = settings.project_root / "storage" / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)
    target = incoming_dir / f"{uuid4().hex}{suffix}"
    bytes_written = 0
    try:
        with target.open("wb") as handle:
            while content := await file.read(1024 * 1024):
                bytes_written += len(content)
                if bytes_written > max_bytes:
                    raise AppError(f"Files must be {settings.max_upload_size_mb} MB or smaller.", status_code=413, code="file_too_large")
                handle.write(content)
    except Exception:
        if target.exists():
            target.unlink()
        raise
    finally:
        await file.close()

    def work(cancel_event, progress):
        analytics = AnalyticsService(settings)
        try:
            result = IngestionService(settings, get_retrieval(settings)).ingest_upload(
                target,
                safe_name,
                cancel_event=cancel_event,
                progress=progress,
            )
        except AppError as exc:
            analytics.log_event("duplicate" if exc.code == "duplicate_document" else "rejected", filename=safe_name, code=exc.code)
            raise
        else:
            analytics.log_event("upload", filename=result.filename, chunks_indexed=result.chunks_indexed, sha256=result.sha256)
            return result.__dict__
        finally:
            if target.exists():
                target.unlink()

    return ingestion_jobs.submit("upload", work).snapshot()


@router.get("/api/ingestion/jobs/{job_id}", response_model=IngestionJobResponse)
def ingestion_job(job_id: str) -> dict:
    job = ingestion_jobs.get(job_id)
    if job is None:
        raise AppError("Ingestion job not found.", status_code=404, code="job_not_found")
    return job


@router.post("/api/ingestion/jobs/{job_id}/cancel", response_model=IngestionJobResponse)
def cancel_ingestion_job(job_id: str) -> dict:
    job = ingestion_jobs.cancel(job_id)
    if job is None:
        raise AppError("Ingestion job not found.", status_code=404, code="job_not_found")
    return job


@router.post("/api/operations/{operation_id}/cancel")
def cancel_operation(operation_id: str) -> dict:
    if not operations.cancel(operation_id):
        raise AppError("Operation not found or already finished.", status_code=404, code="operation_not_found")
    return {"id": operation_id, "status": "cancelling"}


@router.get("/api/documents", response_model=list[DocumentSummary])
def list_documents(retrieval: RetrievalService = Depends(get_retrieval)) -> list[DocumentSummary]:
    return [DocumentSummary(**item) for item in retrieval.list_sources()]


@router.get("/api/chromadb/inspect", response_model=ChromaInspectionResponse)
def inspect_chromadb(
    query: str = "",
    limit: int = 100,
    retrieval: RetrievalService = Depends(get_retrieval),
) -> ChromaInspectionResponse:
    limit = max(1, min(limit, 250))
    return ChromaInspectionResponse(**retrieval.inspect_collection(query=query, limit=limit))


@router.post("/api/retrieval/search", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    x_operation_id: str | None = Header(default=None, alias="X-Operation-ID"),
    retrieval: RetrievalService = Depends(get_retrieval),
) -> SearchResponse:
    cancel_event = operations.register(x_operation_id) if x_operation_id else None
    started = perf_counter()
    try:
        results = await asyncio.to_thread(retrieval.search, request.query, request.mode, request.top_k, request.source, cancel_event=cancel_event)
        latency_ms = round((perf_counter() - started) * 1000, 2)
        confidence = retrieval.confidence(results, query=request.query)
        AnalyticsService(retrieval.settings).log_event(
            "retrieval",
            mode=request.mode,
            top_k=request.top_k or retrieval.settings.top_k,
            result_count=len(results),
            confidence=confidence,
            retrieval_latency_ms=latency_ms,
        )
        return SearchResponse(query=request.query, mode=request.mode, results=results, confidence=confidence, latency_ms=latency_ms)
    finally:
        if x_operation_id:
            operations.release(x_operation_id)


@router.post("/api/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    settings: Settings = Depends(get_settings),
    x_operation_id: str | None = Header(default=None, alias="X-Operation-ID"),
    retrieval: RetrievalService = Depends(get_retrieval),
) -> ChatResponse:
    cancel_event = operations.register(x_operation_id) if x_operation_id else None
    retrieval_started = perf_counter()
    try:
        results = await asyncio.to_thread(retrieval.search, request.query, request.mode, request.top_k, request.source, cancel_event=cancel_event)
        retrieval_latency_ms = round((perf_counter() - retrieval_started) * 1000, 2)
        confidence = retrieval.confidence(results, query=request.query)
        generation_latency_ms = 0.0
        grounded = should_ground_context(confidence, results, query=request.query, min_context_score=settings.min_context_score)
        if grounded:
            generation_started = perf_counter()
            answer = await OllamaClient(settings).generate(request.query, results, request.temperature, cancel_event=cancel_event)
            generation_latency_ms = round((perf_counter() - generation_started) * 1000, 2)
        else:
            answer = "No relevant information was found."
        AnalyticsService(settings).log_event(
            "chat",
            mode=request.mode,
            result_count=len(results),
            confidence=confidence,
            retrieval_latency_ms=retrieval_latency_ms,
            generation_latency_ms=generation_latency_ms,
            grounded=grounded,
        )
        return ChatResponse(
            answer=answer,
            citations=results,
            model=settings.ollama_model,
            retrieval_mode=request.mode,
            confidence=confidence,
            retrieval_latency_ms=retrieval_latency_ms,
            generation_latency_ms=generation_latency_ms,
            sources=_sources(results),
            grounded=grounded,
        )
    finally:
        if x_operation_id:
            operations.release(x_operation_id)


@router.post("/api/evaluation/run", response_model=EvaluationResponse)
async def evaluate(
    request: EvaluationRequest,
    settings: Settings = Depends(get_settings),
    retrieval: RetrievalService = Depends(get_retrieval),
) -> EvaluationResponse:
    service = EvaluationService(retrieval)
    if request.include_ragas and not RAGAS_AVAILABLE:
        raise AppError(
            "RAGAS evaluation is unavailable due to missing optional dependencies.",
            status_code=503,
            code="ragas_unavailable",
        )
    response = await service.run(
        request.questions,
        request.mode,
        request.relevant_sources,
        request.reference_answers,
        settings=settings,
        include_ragas=request.include_ragas,
        source=request.source,
    )
    AnalyticsService(retrieval.settings).log_event(
        "evaluation",
        mode=request.mode,
        questions=len(request.questions),
        average_precision=response.average_precision,
        average_recall=response.average_recall,
        average_latency_ms=response.average_latency_ms,
        average_faithfulness=response.average_faithfulness or 0.0,
        average_answer_correctness=response.average_answer_correctness or 0.0,
        average_context_precision=response.average_context_precision or 0.0,
        average_context_recall=response.average_context_recall or 0.0,
    )
    return response


@router.get("/api/analytics", response_model=AnalyticsResponse)
def analytics(
    settings: Settings = Depends(get_settings),
    retrieval: RetrievalService = Depends(get_retrieval),
) -> AnalyticsResponse:
    stats = retrieval.stats()
    return AnalyticsResponse(**AnalyticsService(settings).summarize(stats["documents"], stats["chunks"]))


@router.get("/api/analytics/export.csv")
def export_analytics(settings: Settings = Depends(get_settings)) -> Response:
    analytics_service = AnalyticsService(settings)
    events = analytics_service.read_events()
    fieldnames = sorted({key for event in events for key in event.keys()}) or ["type", "timestamp"]
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(events)
    return Response(
        content=stream.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=rag_analytics_results.csv"},
    )


def _sources(results) -> list[dict]:
    sources = []
    seen = set()
    for result in results:
        metadata = result.metadata
        key = (result.source, metadata.get("page_number", ""), metadata.get("section", ""))
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            {
                "source": result.source,
                "page_number": metadata.get("page_number", ""),
                "section": metadata.get("section", ""),
                "score": result.score,
            }
        )
    return sources
