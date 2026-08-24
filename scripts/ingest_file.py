from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import configure_logging  # noqa: E402
from app.services.ingestion import IngestionService  # noqa: E402
from app.services.retrieval import RetrievalService  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest a PDF, Excel, CSV, TXT, or Markdown file.")
    parser.add_argument("path", type=Path)
    args = parser.parse_args()

    settings = get_settings()
    configure_logging(settings)
    retrieval = RetrievalService(settings)
    result = IngestionService(settings, retrieval).ingest_upload(args.path)
    print(f"{result.message}: indexed {result.chunks_indexed} chunks from {args.path.name}.")


if __name__ == "__main__":
    main()
