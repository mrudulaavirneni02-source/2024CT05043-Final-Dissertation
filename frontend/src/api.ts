const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "http://127.0.0.1:8000";

export type RetrievalMode = "hybrid" | "vector" | "bm25";

export interface RetrievedChunk {
  id: string;
  text: string;
  source: string;
  score: number;
  metadata: Record<string, unknown>;
}

export interface DocumentSummary {
  source: string;
  chunks: number;
  file_type?: string;
  author?: string;
  upload_date?: string;
}

export interface ChromaRecord {
  id: string;
  source: string;
  text: string;
  metadata: Record<string, unknown>;
}

export interface ChromaInspectionResponse {
  collections: string[];
  collection_name: string;
  vector_count: number;
  document_count: number;
  total_tokens: number;
  documents: DocumentSummary[];
  records: ChromaRecord[];
}

export interface SearchResponse {
  query: string;
  mode: RetrievalMode;
  results: RetrievedChunk[];
  confidence: number;
  latency_ms: number;
}

export interface ChatResponse {
  answer: string;
  citations: RetrievedChunk[];
  model: string;
  retrieval_mode: RetrievalMode;
  confidence: number;
  retrieval_latency_ms: number;
  generation_latency_ms: number;
  sources: { source: string; page_number?: string; section?: string; score: number }[];
  grounded: boolean;
}

export interface EvaluationResponse {
  items: {
    question: string;
    retrieved: number;
    top_score: number;
    average_similarity: number;
    context_available: boolean;
    precision_at_k: number;
    recall_at_k: number;
    mrr: number;
    hit_rate: number;
    retrieval_latency_ms: number;
    faithfulness: number | null;
    answer_correctness: number | null;
    context_precision: number | null;
    context_recall: number | null;
    ragas_error?: string | null;
  }[];
  average_top_score: number;
  average_precision: number;
  average_recall: number;
  average_mrr: number;
  hit_rate: number;
  average_latency_ms: number;
  average_faithfulness: number | null;
  average_answer_correctness: number | null;
  average_context_precision: number | null;
  average_context_recall: number | null;
  ragas_completed: boolean;
  ragas_error?: string | null;
}

export interface AnalyticsResponse {
  documents: number;
  chunks: number;
  queries: number;
  duplicate_files: number;
  rejected_files: number;
  average_retrieval_latency_ms: number;
  average_generation_latency_ms: number;
  average_confidence: number;
  embedding_model: string;
  retrievers: string[];
  llm: string;
  events_by_type: Record<string, number>;
}

export interface IngestionJob {
  id: string;
  kind: string;
  status: "queued" | "running" | "cancelling" | "completed" | "cancelled" | "failed";
  phase: string;
  completed: number;
  total: number;
  result?: { filename: string; chunks_indexed: number; message: string; sha256?: string; validation_status: string; duplicate: boolean };
  error?: { code: string; message: string };
  created_at: string;
  finished_at?: string;
}

export interface RequestOptions {
  signal?: AbortSignal;
  operationId?: string;
}

async function request<T>(path: string, init?: RequestInit, options?: RequestOptions): Promise<T> {
  const headers = new Headers(init?.headers);
  if (options?.operationId) headers.set("X-Operation-ID", options.operationId);
  const response = await fetch(`${API_BASE}${path}`, { ...init, headers, signal: options?.signal ?? init?.signal });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = data?.error?.message ?? `Request failed with ${response.status}`;
    throw new Error(message);
  }
  return data as T;
}

export function health() {
  return request<{
    status: string;
    app_name: string;
    environment: string;
    embedding_model: string;
    ollama_model: string;
    storage_ready: boolean;
    ragas_available: boolean;
    ragas_version?: string | null;
  }>("/health");
}

export function ingestSeed(options?: RequestOptions) {
  return request<IngestionJob>("/api/documents/ingest-seed", {
    method: "POST",
    signal: options?.signal,
  }, options);
}

export function reindexLibrary(options?: RequestOptions) {
  return request<IngestionJob>("/api/documents/reindex", {
    method: "POST",
    signal: options?.signal,
  }, options);
}

export function listDocuments() {
  return request<DocumentSummary[]>("/api/documents");
}

export function inspectChroma(query = "", limit = 100) {
  const params = new URLSearchParams({ query, limit: String(limit) });
  return request<ChromaInspectionResponse>(`/api/chromadb/inspect?${params}`);
}

export function uploadDocument(file: File, options?: RequestOptions) {
  const form = new FormData();
  form.append("file", file);
  return request<IngestionJob>("/api/documents/upload", {
    method: "POST",
    body: form,
    signal: options?.signal,
  }, options);
}

export function ingestionJob(id: string, options?: RequestOptions) {
  return request<IngestionJob>(`/api/ingestion/jobs/${id}`, { signal: options?.signal }, options);
}

export function cancelIngestionJob(id: string) {
  return request<IngestionJob>(`/api/ingestion/jobs/${id}/cancel`, { method: "POST" });
}

export function cancelOperation(id: string) {
  return request<{ id: string; status: string }>(`/api/operations/${id}/cancel`, { method: "POST" });
}

export function search(query: string, mode: RetrievalMode, source?: string, options?: RequestOptions) {
  return request<SearchResponse>("/api/retrieval/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, mode, source }),
    signal: options?.signal,
  }, options);
}

export function chat(query: string, mode: RetrievalMode, source?: string, options?: RequestOptions) {
  return request<ChatResponse>("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, mode, source, temperature: 0.2 }),
    signal: options?.signal,
  }, options);
}

export function evaluate(
  questions: string[],
  mode: RetrievalMode,
  includeRagas = true,
  referenceAnswers?: Record<string, string>,
  relevantSources?: Record<string, string[]>,
  source?: string,
) {
  return request<EvaluationResponse>("/api/evaluation/run", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ questions, mode, include_ragas: includeRagas, reference_answers: referenceAnswers, relevant_sources: relevantSources, source }),
  });
}

export function analytics() {
  return request<AnalyticsResponse>("/api/analytics");
}
