from __future__ import annotations

import sys
from pathlib import Path

import uvicorn


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "app.main:app",
        app_dir=str(BACKEND),
        host=settings.backend_host,
        port=settings.backend_port,
        reload=settings.app_env == "development",
    )


if __name__ == "__main__":
    main()
