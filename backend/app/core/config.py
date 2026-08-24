from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "2024CT05043 Knowledge Extraction RAG"
    app_env: str = "development"
    log_level: str = "INFO"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_origin: str = "http://localhost:5173"

    data_dir: Path = Path("./data")
    upload_dir: Path = Path("./storage/uploads")
    chroma_dir: Path = Path("./storage/chroma")
    bm25_dir: Path = Path("./storage/bm25")
    log_dir: Path = Path("./logs")

    embedding_model: str = "BAAI/bge-base-en-v1.5"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_timeout_seconds: int = 180

    # Chunk sizes are measured in words.  Evidence-sized chunks keep unrelated
    # sections of a paper from being retrieved and cited as one answer.
    chunk_size: int = Field(default=700, ge=200, le=12000)
    chunk_overlap: int = Field(default=100, ge=0, le=4000)
    semantic_min_chunk_ratio: float = Field(default=0.65, ge=0.2, le=1)
    embedding_batch_size: int = Field(default=32, ge=1, le=256)
    max_upload_size_mb: int = Field(default=50, ge=1, le=1024)
    top_k: int = Field(default=4, ge=1, le=20)
    hybrid_vector_weight: float = Field(default=0.68, ge=0, le=1)
    hybrid_bm25_weight: float = Field(default=0.32, ge=0, le=1)
    min_context_score: float = Field(default=0.35, ge=0, le=1)
    min_query_coverage: float = Field(default=0.5, ge=0, le=1)
    ollama_num_ctx: int = Field(default=8192, ge=1024, le=32768)
    ollama_num_predict: int = Field(default=220, ge=32, le=2048)
    ollama_keep_alive: str = "10m"
    ragas_ollama_model: str | None = None

    enable_ocr_fallback: bool = True
    tesseract_cmd: str = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
    ocr_language: str = "eng"

    @field_validator("data_dir", "upload_dir", "chroma_dir", "bm25_dir", "log_dir", mode="after")
    @classmethod
    def resolve_paths(cls, value: Path) -> Path:
        root = Path(__file__).resolve().parents[3]
        return value if value.is_absolute() else root / value

    @field_validator("hybrid_bm25_weight", mode="after")
    @classmethod
    def validate_hybrid_weights(cls, value: float, info):
        vector_weight = info.data.get("hybrid_vector_weight", 0.68)
        if abs((vector_weight + value) - 1.0) > 0.001:
            raise ValueError("HYBRID_VECTOR_WEIGHT and HYBRID_BM25_WEIGHT must add up to 1.0")
        return value

    @property
    def project_root(self) -> Path:
        return Path(__file__).resolve().parents[3]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    for directory in [settings.data_dir, settings.upload_dir, settings.chroma_dir, settings.bm25_dir, settings.log_dir]:
        directory.mkdir(parents=True, exist_ok=True)
    return settings
