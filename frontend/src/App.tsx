import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import {
  Activity,
  BarChart3,
  CheckCircle2,
  Database,
  FileSearch,
  Gauge,
  MessageSquareText,
  Play,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Square,
  Upload,
} from "lucide-react";
import {
  AnalyticsResponse,
  ChatResponse,
  ChromaInspectionResponse,
  DocumentSummary,
  EvaluationResponse,
  IngestionJob,
  RetrievedChunk,
  RetrievalMode,
  SearchResponse,
  analytics,
  cancelIngestionJob,
  cancelOperation,
  chat,
  evaluate,
  health,
  ingestSeed,
  ingestionJob,
  inspectChroma,
  listDocuments,
  reindexLibrary,
  search,
  uploadDocument,
} from "./api";

type HealthState = Awaited<ReturnType<typeof health>> | null;
type ComparisonState = Partial<Record<RetrievalMode, EvaluationResponse>>;

const modes: RetrievalMode[] = ["hybrid", "vector", "bm25"];
const sampleQuestions = [
  "What is the broad area of work for the dissertation?",
  "How does the framework compare vector-based and vectorless retrieval?",
  "What quality controls are planned to reduce hallucinations?",
].join("\n");

export default function App() {
  const [status, setStatus] = useState<HealthState>(null);
  const [activeTab, setActiveTab] = useState<"workspace" | "chromadb">("workspace");
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [analyticsState, setAnalyticsState] = useState<AnalyticsResponse | null>(null);
  const [mode, setMode] = useState<RetrievalMode>("hybrid");
  const [sourceFilter, setSourceFilter] = useState("");
  const [query, setQuery] = useState("How does the dissertation framework use ChromaDB and BM25?");
  const [answer, setAnswer] = useState<ChatResponse | null>(null);
  const [searchState, setSearchState] = useState<SearchResponse | null>(null);
  const [results, setResults] = useState<RetrievedChunk[]>([]);
  const [questions, setQuestions] = useState(sampleQuestions);
  const [referenceAnswers, setReferenceAnswers] = useState<Record<string, string>>({});
  const [relevantSources, setRelevantSources] = useState<Record<string, string>>({});
  const [evaluation, setEvaluation] = useState<EvaluationResponse | null>(null);
  const [comparison, setComparison] = useState<ComparisonState>({});
  const [chromaInspection, setChromaInspection] = useState<ChromaInspectionResponse | null>(null);
  const [storedQuery, setStoredQuery] = useState("");
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [activeIngestion, setActiveIngestion] = useState<IngestionJob | null>(null);
  const activeController = useRef<AbortController | null>(null);
  const activeOperationId = useRef<string | null>(null);
  const activeJobId = useRef<string | null>(null);

  const totalChunks = useMemo(() => documents.reduce((sum, item) => sum + item.chunks, 0), [documents]);
  const confidence = answer?.confidence ?? searchState?.confidence ?? analyticsState?.average_confidence ?? 0;

  async function refresh() {
    setError("");
    const [healthData, docs, stats] = await Promise.all([health(), listDocuments(), analytics()]);
    setStatus(healthData);
    setDocuments(docs);
    setAnalyticsState(stats);
  }

  async function loadChroma(query = storedQuery) {
    setBusy("chromadb");
    setError("");
    try {
      setChromaInspection(await inspectChroma(query));
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not inspect ChromaDB");
    } finally {
      setBusy("");
    }
  }

  function selectTab(tab: "workspace" | "chromadb") {
    setActiveTab(tab);
    if (tab === "chromadb") void loadChroma();
  }

  async function runAction<T>(label: string, action: () => Promise<T>, success?: (value: T) => string) {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      const value = await action();
      if (success) setNotice(success(value));
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unexpected error");
    } finally {
      setBusy("");
    }
  }

  async function startIngestion(start: (signal: AbortSignal) => Promise<IngestionJob>, initialBusy: string) {
    const controller = new AbortController();
    activeController.current = controller;
    activeJobId.current = null;
    setBusy(initialBusy);
    setError("");
    setNotice("");
    try {
      const job = await start(controller.signal);
      activeJobId.current = job.id;
      setActiveIngestion(job);
      setBusy("ingestion");
      const completed = await waitForIngestion(job.id, controller.signal);
      if (completed.status === "completed" && completed.result) {
        const hash = completed.result.sha256 ? ` SHA256 ${completed.result.sha256.slice(0, 12)}...` : "";
        setNotice(`${completed.result.message}: ${completed.result.filename}, ${completed.result.chunks_indexed} chunks.${hash}`);
        await refresh();
      } else if (completed.status === "failed") {
        setError(completed.error?.message ?? "Ingestion failed");
      } else {
        setNotice("Ingestion cancelled. No partial document was retained.");
      }
    } catch (err) {
      if (!isCancelled(err)) setError(err instanceof Error ? err.message : "Ingestion failed");
    } finally {
      clearActive(controller);
      setActiveIngestion(null);
      setBusy("");
    }
  }

  async function waitForIngestion(jobId: string, signal: AbortSignal): Promise<IngestionJob> {
    while (true) {
      const job = await ingestionJob(jobId, { signal });
      setActiveIngestion(job);
      if (["completed", "failed", "cancelled"].includes(job.status)) return job;
      await delay(450, signal);
    }
  }

  async function cancelIngestion() {
    const jobId = activeJobId.current;
    if (jobId) {
      setBusy("cancelling");
      try {
        await cancelIngestionJob(jobId);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Could not cancel ingestion");
      }
      return;
    }
    activeController.current?.abort();
    setNotice("Upload cancelled.");
  }

  async function runRetrieval(kind: "search" | "chat") {
    const controller = new AbortController();
    const operationId = crypto.randomUUID();
    activeController.current = controller;
    activeOperationId.current = operationId;
    setBusy(kind);
    setError("");
    setNotice("");
    try {
      if (kind === "search") {
        const response = await search(query, mode, sourceFilter || undefined, { signal: controller.signal, operationId });
        setSearchState(response);
        setResults(response.results);
        setNotice(`${response.results.length} chunks | confidence ${percent(response.confidence)} | ${response.latency_ms} ms`);
      } else {
        const response = await chat(query, mode, sourceFilter || undefined, { signal: controller.signal, operationId });
        setAnswer(response);
        setResults(response.citations);
        setNotice(`${response.grounded ? "Answered" : "Blocked hallucination"} | confidence ${percent(response.confidence)}`);
      }
      await refresh();
    } catch (err) {
      if (isCancelled(err)) setNotice("Retrieval cancelled.");
      else setError(err instanceof Error ? err.message : "Retrieval failed");
    } finally {
      clearActive(controller);
      setBusy("");
    }
  }

  function cancelRetrieval() {
    const operationId = activeOperationId.current;
    if (operationId) void cancelOperation(operationId).catch(() => undefined);
    activeController.current?.abort();
    setNotice("Cancelling retrieval...");
  }

  function clearActive(controller: AbortController) {
    if (activeController.current === controller) {
      activeController.current = null;
      activeOperationId.current = null;
      activeJobId.current = null;
    }
  }

  async function runEvaluation() {
    setBusy("evaluate");
    setError("");
    setNotice("");
    try {
      const response = await evaluate(questionList(), mode, true, nonEmptyReferences(), sourceLabels(), sourceFilter || undefined);
      setEvaluation(response);
      const ragasStatus = response.ragas_completed && response.average_faithfulness !== null
        ? `Faithfulness ${percent(response.average_faithfulness)}`
        : "RAGAS unavailable for this run; retrieval metrics are still shown";
      setNotice(`${ragasStatus} | Precision ${percent(response.average_precision)} | Recall ${percent(response.average_recall)}`);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Evaluation failed");
    } finally {
      setBusy("");
    }
  }

  async function compareRetrievers() {
    setBusy("compare-retrievers");
    setError("");
    setNotice("");
    try {
      const entries = await Promise.all(modes.map(async (item) => [item, await evaluate(questionList(), item, false, undefined, undefined, sourceFilter || undefined)] as const));
      setComparison(Object.fromEntries(entries));
      setNotice("BM25, vector, and hybrid retrieval compared without RAGAS judge calls.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Comparison failed");
    } finally {
      setBusy("");
    }
  }

  function questionList() {
    return questions.split("\n").map((item) => item.trim()).filter(Boolean);
  }

  function nonEmptyReferences() {
    return Object.fromEntries(Object.entries(referenceAnswers).filter(([, answer]) => answer.trim()));
  }

  function sourceLabels() {
    return Object.fromEntries(
      Object.entries(relevantSources)
        .map(([question, sources]) => [question, sources.split(",").map((source) => source.trim()).filter(Boolean)])
        .filter(([, sources]) => sources.length),
    );
  }

  useEffect(() => {
    refresh().catch((err) => setError(err instanceof Error ? err.message : "Backend is not reachable"));
    return () => activeController.current?.abort();
  }, []);

  return (
    <main className="app-shell">
      <header className="topbar">
        <div>
          <h1>2024CT05043 Knowledge Extraction RAG</h1>
          <p>{status?.embedding_model ?? "Embedding model"} | {status?.ollama_model ?? "Ollama"} | {status?.environment ?? "development"}</p>
        </div>
        <button className="icon-button" onClick={() => runAction("refresh", refresh)} title="Refresh">
          <RefreshCw size={18} />
        </button>
      </header>

      <nav className="app-tabs" aria-label="Application sections">
        <button className={activeTab === "workspace" ? "active" : ""} onClick={() => selectTab("workspace")}>
          <MessageSquareText size={16} /> Workspace
        </button>
        <button className={activeTab === "chromadb" ? "active" : ""} onClick={() => selectTab("chromadb")}>
          <Database size={16} /> ChromaDB
        </button>
      </nav>

      {(notice || error) && <section className={`notice ${error ? "error" : ""}`}>{error || notice}</section>}

      {activeTab === "workspace" ? <>
      <section className="metrics-grid">
        <Metric icon={<Activity size={19} />} label="Backend" value={status?.status ?? "unknown"} detail={status?.storage_ready ? "storage ready" : "checking"} />
        <Metric icon={<Database size={19} />} label="Documents" value={String(documents.length)} detail={`${totalChunks} chunks`} />
        <Metric icon={<Gauge size={19} />} label="Confidence" value={percent(confidence)} detail={`${analyticsState?.queries ?? 0} queries`} />
        <Metric icon={<ShieldCheck size={19} />} label="Rejected" value={String(analyticsState?.rejected_files ?? 0)} detail={`${analyticsState?.duplicate_files ?? 0} duplicates`} />
        <Metric icon={<Sparkles size={19} />} label="RAGAS" value={status?.ragas_available ? "ready" : "unavailable"} detail={status?.ragas_version ? `v${status.ragas_version}` : "dependency check"} />
      </section>

      <section className="workspace-grid">
        <div className="panel">
          <PanelTitle icon={<Upload size={18} />} title="Upload" />
          <div
            className={`dropzone ${dragging ? "active" : ""}`}
            onDragOver={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragging(false);
              const file = event.dataTransfer.files?.[0];
              if (file && !busy) void startIngestion((signal) => uploadDocument(file, { signal }), "upload");
            }}
          >
            <Upload size={22} />
            <strong>Drop document</strong>
            <span>PDF, DOCX, TXT, CSV, Excel, Markdown</span>
          </div>
          <div className="button-row">
            <button disabled={Boolean(busy)} onClick={() => void startIngestion((signal) => ingestSeed({ signal }), "ingestion")}>
              <Play size={16} /> Ingest Seed
            </button>
            <button disabled={Boolean(busy)} onClick={() => void startIngestion((signal) => reindexLibrary({ signal }), "reindex")} title="Rebuilds the index from retained uploads using the current chunking settings">
              <RefreshCw size={16} /> Re-index Library
            </button>
            <label className="file-button">
              <Upload size={16} /> Choose File
              <input
                type="file"
                accept=".pdf,.docx,.txt,.md,.xlsx,.xls,.csv"
                disabled={Boolean(busy)}
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) void startIngestion((signal) => uploadDocument(file, { signal }), "upload");
                  event.currentTarget.value = "";
                }}
              />
            </label>
            {(["upload", "ingestion", "reindex", "cancelling"].includes(busy)) && (
              <button className="danger-button" onClick={() => void cancelIngestion()}>
                <Square size={16} /> Cancel
              </button>
            )}
          </div>
          {(["upload", "ingestion", "reindex", "cancelling"].includes(busy)) && (
            <Progress label={activeIngestion ? `${activeIngestion.phase}${activeIngestion.total ? ` (${activeIngestion.completed}/${activeIngestion.total})` : ""}` : "Uploading file"} />
          )}
        </div>

        <div className="panel">
          <PanelTitle icon={<Search size={18} />} title="Retrieval" />
          <ModeControl value={mode} onChange={setMode} />
          <label className="source-filter">
            Search scope
            <select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}>
              <option value="">All indexed documents</option>
              {documents.map((document) => <option key={document.source} value={document.source}>{document.source}</option>)}
            </select>
          </label>
          <textarea value={query} onChange={(event) => setQuery(event.target.value)} />
          <div className="button-row">
            <button disabled={Boolean(busy)} onClick={() => void runRetrieval("search")}>
              <Search size={16} /> Search
            </button>
            <button disabled={Boolean(busy)} onClick={() => void runRetrieval("chat")}>
              <MessageSquareText size={16} /> Ask
            </button>
            {(["search", "chat"].includes(busy)) && (
              <button className="danger-button" onClick={cancelRetrieval}>
                <Square size={16} /> Stop
              </button>
            )}
          </div>
        </div>

        <div className="panel wide">
          <PanelTitle icon={<MessageSquareText size={18} />} title="Answer" />
          <div className="answer-meta">
            <Badge icon={<Gauge size={15} />} label={percent(answer?.confidence ?? searchState?.confidence ?? 0)} />
            <Badge icon={<Activity size={15} />} label={`${answer?.retrieval_latency_ms ?? searchState?.latency_ms ?? 0} ms retrieval`} />
            <Badge icon={<CheckCircle2 size={15} />} label={answer ? (answer.grounded ? "grounded" : "no answer") : "ready"} />
          </div>
          <div className="answer">{answer?.answer || "Ask a question to generate a grounded answer from indexed context."}</div>
          {answer?.sources?.length ? <SourceList sources={answer.sources} /> : null}
          <ResultList results={results} />
        </div>

        <div className="panel">
          <PanelTitle icon={<Database size={18} />} title="Documents" />
          <div className="document-list">
            {documents.length === 0 ? (
              <p className="empty">No indexed documents yet.</p>
            ) : (
              documents.map((doc) => (
                <div className="document-row" key={doc.source}>
                  <span>{doc.source}<small>{doc.file_type ?? "unknown"} | {doc.author ?? "unknown"}</small></span>
                  <strong>{doc.chunks}</strong>
                </div>
              ))
            )}
          </div>
        </div>

        <div className="panel">
          <PanelTitle icon={<BarChart3 size={18} />} title="Evaluation" />
          <textarea value={questions} onChange={(event) => setQuestions(event.target.value)} />
          <div className="evaluation-dataset">
            <p>Add a verified reference answer and the relevant source filename for every question. Without a relevant source, retrieval precision, recall, MRR, and hit rate are intentionally reported as zero rather than estimated from similarity scores.</p>
            {questionList().map((evaluationQuestion) => (
              <div className="evaluation-case" key={evaluationQuestion}>
                <strong>{evaluationQuestion}</strong>
                <textarea
                  aria-label={`Reference answer for ${evaluationQuestion}`}
                  value={referenceAnswers[evaluationQuestion] ?? ""}
                  onChange={(event) => setReferenceAnswers((current) => ({ ...current, [evaluationQuestion]: event.target.value }))}
                  placeholder="Reference answer (required for answer correctness and context recall/precision)"
                />
                {looksLikeRefusal(referenceAnswers[evaluationQuestion] ?? "") && <small className="reference-warning">This reads like a model refusal, not a reference answer. Replace it with the verified answer from the selected document.</small>}
                <input
                  aria-label={`Relevant sources for ${evaluationQuestion}`}
                  value={relevantSources[evaluationQuestion] ?? ""}
                  onChange={(event) => setRelevantSources((current) => ({ ...current, [evaluationQuestion]: event.target.value }))}
                  placeholder="Relevant source filenames, comma-separated (required for retrieval metrics)"
                />
              </div>
            ))}
          </div>
          <div className="button-row">
            <button disabled={Boolean(busy)} onClick={runEvaluation}>
              <Activity size={16} /> Run
            </button>
            <button disabled={Boolean(busy)} onClick={compareRetrievers}>
              <BarChart3 size={16} /> Compare
            </button>
          </div>
          {evaluation && <EvaluationSummary evaluation={evaluation} />}
          <RagasSummary evaluation={evaluation} available={status?.ragas_available ?? false} model={status?.ollama_model} />
          {Object.keys(comparison).length > 0 && <ComparisonTable comparison={comparison} />}
        </div>
      </section>

      {analyticsState && (
        <section className="panel dashboard">
          <PanelTitle icon={<BarChart3 size={18} />} title="Analytics" />
          <div className="analytics-grid">
            <MiniStat label="Avg Precision" value={evaluation ? percent(evaluation.average_precision) : "-"} />
            <MiniStat label="Avg Recall" value={evaluation ? percent(evaluation.average_recall) : "-"} />
            <MiniStat label="Retrieval" value={`${analyticsState.average_retrieval_latency_ms} ms`} />
            <MiniStat label="Generation" value={`${analyticsState.average_generation_latency_ms} ms`} />
          </div>
          <div className="bars">
            <Bar label="Confidence" value={analyticsState.average_confidence} />
            <Bar label="Hit Rate" value={evaluation?.hit_rate ?? 0} />
            <Bar label="MRR" value={evaluation?.average_mrr ?? 0} />
          </div>
        </section>
      )}
      </> : (
        <ChromaBrowser
          inspection={chromaInspection}
          loading={busy === "chromadb"}
          query={storedQuery}
          onQueryChange={setStoredQuery}
          onSearch={() => void loadChroma()}
        />
      )}

      {busy && <div className="busy">Working: {busy}</div>}
    </main>
  );
}

function Metric({ icon, label, value, detail }: { icon: ReactNode; label: string; value: string; detail: string }) {
  return (
    <div className="metric">
      <div className="metric-icon">{icon}</div>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{detail}</small>
      </div>
    </div>
  );
}

function MiniStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="mini-stat">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function PanelTitle({ icon, title }: { icon: ReactNode; title: string }) {
  return (
    <div className="panel-title">
      {icon}
      <h2>{title}</h2>
    </div>
  );
}

function Badge({ icon, label }: { icon: ReactNode; label: string }) {
  return (
    <span className="badge">{icon}{label}</span>
  );
}

function ModeControl({ value, onChange }: { value: RetrievalMode; onChange: (mode: RetrievalMode) => void }) {
  return (
    <div className="segmented">
      {modes.map((item) => (
        <button className={value === item ? "active" : ""} key={item} onClick={() => onChange(item)}>
          {item}
        </button>
      ))}
    </div>
  );
}

function SourceList({ sources }: { sources: ChatResponse["sources"] }) {
  return (
    <div className="source-list">
      {sources.map((source) => (
        <div className="source-pill" key={`${source.source}-${source.page_number}-${source.section}`}>
          <strong>{source.source}</strong>
          <span>Page {source.page_number || "n/a"} | {source.section || "section n/a"} | {source.score.toFixed(3)}</span>
        </div>
      ))}
    </div>
  );
}

function ResultList({ results }: { results: RetrievedChunk[] }) {
  return (
    <div className="results">
      {results.map((result) => (
        <article className="result" key={result.id}>
          <div>
            <strong>{result.source}</strong>
            <span>{result.score.toFixed(3)}</span>
          </div>
          <small>
            Page {String(result.metadata.page_number || "n/a")} | {String(result.metadata.section || "section n/a")} | {String(result.metadata.token_count || 0)} tokens
          </small>
          <p>{result.text}</p>
        </article>
      ))}
    </div>
  );
}

function EvaluationSummary({ evaluation }: { evaluation: EvaluationResponse }) {
  return (
    <div className="eval-summary">
      <MiniStat label="Precision" value={percent(evaluation.average_precision)} />
      <MiniStat label="Recall" value={percent(evaluation.average_recall)} />
      <MiniStat label="MRR" value={evaluation.average_mrr.toFixed(3)} />
      <MiniStat label="Latency" value={`${evaluation.average_latency_ms} ms`} />
    </div>
  );
}

function RagasSummary({ evaluation, available, model }: { evaluation: EvaluationResponse | null; available: boolean; model?: string }) {
  return (
    <section className="ragas-summary">
      <div className="ragas-heading">
        <span><Sparkles size={16} /> RAGAS faithfulness</span>
        <small>{available ? `uses ${model ?? "the configured Ollama model"}` : "dependencies unavailable"}</small>
      </div>
      {evaluation ? (
        <div className="eval-summary">
          <MiniStat label="Faithfulness" value={evaluation.average_faithfulness === null ? "not scored" : percent(evaluation.average_faithfulness)} />
          <MiniStat label="Answer correctness" value={metricValue(evaluation.average_answer_correctness, "add reference")} />
          <MiniStat label="Context precision" value={metricValue(evaluation.average_context_precision, "add reference")} />
          <MiniStat label="Context recall" value={metricValue(evaluation.average_context_recall, "add reference")} />
        </div>
      ) : (
        <p className="empty">Run evaluation to score whether generated answers are supported by retrieved context.</p>
      )}
      {evaluation?.ragas_error && <p className="ragas-error">RAGAS did not finish: {evaluation.ragas_error}</p>}
    </section>
  );
}

function ChromaBrowser({
  inspection,
  loading,
  query,
  onQueryChange,
  onSearch,
}: {
  inspection: ChromaInspectionResponse | null;
  loading: boolean;
  query: string;
  onQueryChange: (query: string) => void;
  onSearch: () => void;
}) {
  return (
    <section className="chroma-page">
      <div className="chroma-header">
        <div>
          <h2>ChromaDB Browser</h2>
          <p>{inspection?.collection_name ?? "knowledge_chunks"} · {inspection?.collections.length ?? 0} collection{(inspection?.collections.length ?? 0) === 1 ? "" : "s"}</p>
        </div>
        <form className="stored-search" onSubmit={(event) => { event.preventDefault(); onSearch(); }}>
          <input value={query} onChange={(event) => onQueryChange(event.target.value)} placeholder="Search text, source, ID, or metadata" />
          <button disabled={loading}><Search size={16} /> Search records</button>
        </form>
      </div>

      <div className="metrics-grid chroma-metrics">
        <Metric icon={<Database size={19} />} label="Collections" value={String(inspection?.collections.length ?? 0)} detail={inspection?.collection_name ?? "loading collection"} />
        <Metric icon={<FileSearch size={19} />} label="Vectors / chunks" value={formatNumber(inspection?.vector_count ?? 0)} detail="persisted embeddings" />
        <Metric icon={<Database size={19} />} label="Documents" value={formatNumber(inspection?.document_count ?? 0)} detail="unique sources" />
        <Metric icon={<Activity size={19} />} label="Stored tokens" value={formatNumber(inspection?.total_tokens ?? 0)} detail="from indexed chunks" />
      </div>

      <section className="panel chroma-panel">
        <PanelTitle icon={<FileSearch size={18} />} title="Indexed documents" />
        {inspection?.documents.length ? (
          <div className="storage-table">
            <div className="storage-row storage-head"><span>Document</span><span>Type</span><span>Chunks</span><span>Author</span><span>Ingested</span></div>
            {inspection.documents.map((document) => (
              <div className="storage-row" key={document.source}>
                <strong>{document.source}</strong><span>{document.file_type ?? "unknown"}</span><span>{document.chunks}</span><span>{document.author ?? "—"}</span><span>{document.upload_date ?? "—"}</span>
              </div>
            ))}
          </div>
        ) : <p className="empty">No indexed documents yet. Ingest a document, then return here to inspect its vectors.</p>}
      </section>

      <section className="panel chroma-panel">
        <PanelTitle icon={<Database size={18} />} title={query ? `Stored records matching “${query}”` : "Stored records"} />
        <p className="chroma-help">Showing up to 100 stored chunk records. Expand metadata to inspect the exact ChromaDB fields.</p>
        {inspection?.records.length ? (
          <div className="record-list">
            {inspection.records.map((record) => (
              <article className="stored-record" key={record.id}>
                <div className="record-meta"><strong>{record.source}</strong><code>{record.id}</code></div>
                <p>{record.text}</p>
                <details><summary>View metadata</summary><pre>{JSON.stringify(record.metadata, null, 2)}</pre></details>
              </article>
            ))}
          </div>
        ) : <p className="empty">{loading ? "Loading stored records…" : "No stored records match this search."}</p>}
      </section>
    </section>
  );
}

function ComparisonTable({ comparison }: { comparison: ComparisonState }) {
  return (
    <div className="comparison">
      {modes.map((item) => {
        const row = comparison[item];
        return (
          <div className="comparison-row" key={item}>
            <strong>{item}</strong>
            <span>{row ? percent(row.average_precision) : "-"}</span>
            <span>{row ? percent(row.average_recall) : "-"}</span>
            <span>{row ? `${row.average_latency_ms} ms` : "-"}</span>
          </div>
        );
      })}
    </div>
  );
}

function Progress({ label }: { label: string }) {
  return (
    <div className="progress">
      <span>{label}</span>
      <div><i /></div>
    </div>
  );
}

function Bar({ label, value }: { label: string; value: number }) {
  return (
    <div className="bar-row">
      <span>{label}</span>
      <div><i style={{ width: `${Math.max(0, Math.min(100, value * 100))}%` }} /></div>
      <strong>{percent(value)}</strong>
    </div>
  );
}

function percent(value: number) {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

function formatNumber(value: number) {
  return new Intl.NumberFormat().format(value);
}

function metricValue(value: number | null, emptyLabel: string) {
  return value === null ? emptyLabel : percent(value);
}

function looksLikeRefusal(value: string) {
  return /there is no (mention|information)|not (mentioned|relevant)|cannot (find|provide)|not enough (context|information)/i.test(value);
}

function delay(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timeout);
      reject(new DOMException("Operation cancelled", "AbortError"));
    }, { once: true });
  });
}

function isCancelled(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError" || error instanceof Error && error.message === "Operation cancelled.";
}
