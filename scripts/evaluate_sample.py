from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.core.config import get_settings  # noqa: E402
from app.services.evaluation import EvaluationService  # noqa: E402
from app.services.retrieval import RetrievalService  # noqa: E402


QUESTIONS = [
    "What is the broad area of work for the dissertation?",
    "How does the project compare vector and vectorless retrieval?",
    "Which quality controls reduce hallucination risk?",
]


def main() -> None:
    settings = get_settings()
    retrieval = RetrievalService(settings)
    result = EvaluationService(retrieval).run(QUESTIONS, "hybrid")
    for item in result.items:
        print(f"{item.question} -> retrieved={item.retrieved}, top_score={item.top_score:.3f}")
    print(f"Average top score: {result.average_top_score:.3f}")


if __name__ == "__main__":
    main()
