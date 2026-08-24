# 2024CT05043 Knowledge Extraction RAG

Production-oriented local RAG project for the dissertation topic **Intelligent Knowledge Extraction from Unstructured Multimodal Data using RAG-based Evaluation Frameworks**.

This implementation focuses on the requested scope:

- PDF and Excel ingestion
- PDF, DOCX, TXT, CSV, Excel, and Markdown validation/extraction
- SHA256 duplicate detection with duplicate indexing rejection
- corrupt/empty/password-protected upload handling with friendly API errors
- semantic-aware chunking with per-chunk metadata
- local embedding model with ChromaDB semantic retrieval
- BM25 vectorless retrieval for comparison
- hybrid retrieval
- retrieval confidence estimation and low-confidence no-answer behavior
- source citations with filename, page, section, score, and metadata
- local Ollama answer generation
- FastAPI backend with health checks, analytics logging, validation, error handling, and tests
- React/Vite admin and chat UI
- Python setup, ingestion, evaluation, and run scripts

## OCR and Chunking

Tesseract OCR is used automatically for PDF pages that do not contain embedded
text. Pages with native text are extracted directly. If OCR cannot read an
image, that page is skipped and ingestion continues with all available text.

Tesseract is installed at `C:\Program Files\Tesseract-OCR\tesseract.exe` and is
configured through `TESSERACT_CMD` in `.env`. The default chunks are 3,000 words
with a 0.85 semantic-boundary threshold, which substantially reduces needless
fragmentation. Adjust `CHUNK_SIZE` and `SEMANTIC_MIN_CHUNK_RATIO` in `.env` if a
different document type needs finer-grained retrieval.

## Quick Start

From this folder:

```powershell
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
.\.venv\Scripts\python.exe scripts\ingest_seed.py
.\.venv\Scripts\python.exe scripts\run_backend.py
```

In a second terminal:

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

Open `http://localhost:5173`.

## Local Models

The project uses:

- embeddings: `BAAI/bge-base-en-v1.5`
- LLM: `llama3.1:8b` through Ollama

If you want a smaller local LLM later, change `OLLAMA_MODEL` in `.env` to a model you have installed, such as `mistral:7b`, `gemma2:9b`, or `llama3.2:3b`.

## Source Notes

The supplied PDF is image-based, so plain PDF text extraction returns no text. A clean seed transcript derived from visual inspection is included at `data/seed/2024CT05043_abstract_clean.txt`; the original PDF is kept at `data/source/2024CT05043.pdf`.

## API

- `GET /health` - service health, model config, storage checks
- `POST /api/documents/upload` - upload PDF, DOCX, TXT, CSV, Excel, or Markdown files
- `POST /api/documents/ingest-seed` - ingest the clean dissertation seed transcript
- `GET /api/documents` - list indexed document sources
- `POST /api/retrieval/search` - retrieve context without generation; returns confidence and latency
- `POST /api/chat` - retrieve and answer with Ollama; blocks low-confidence hallucinations
- `POST /api/evaluation/run` - Precision@k, Recall@k, MRR, Hit Rate, similarity, and latency
- `GET /api/analytics` - dashboard counters for documents, chunks, queries, latency, confidence, duplicates, and rejections

## Testing

```powershell
.\.venv\Scripts\python.exe -m pytest backend\tests
cd frontend
npm.cmd run build
```

## Deployment Notes

See `docs/DEPLOYMENT.md`.
