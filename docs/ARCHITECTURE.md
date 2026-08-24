# Architecture

```mermaid
flowchart TD
    A["PDF / Excel Uploads"] --> B["Extraction"]
    B --> C["Cleaning and Validation"]
    C --> D["Chunking and Metadata"]
    D --> E["SentenceTransformer Embeddings"]
    D --> F["BM25 Index"]
    E --> G["ChromaDB"]
    G --> H["Hybrid Retrieval"]
    F --> H
    H --> I["Ollama LLM"]
    I --> J["Grounded Answer + Citations"]
    H --> K["Evaluation Metrics"]
```

The app is specialized for the 2024CT05043 dissertation theme: intelligent knowledge extraction from unstructured enterprise data using RAG evaluation frameworks. It implements the requested PDF and Excel portion of the broader multimodal concept.
