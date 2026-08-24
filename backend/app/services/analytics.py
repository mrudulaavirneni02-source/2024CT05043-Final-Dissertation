import json
from collections import Counter
from datetime import UTC, datetime
from statistics import mean

from app.core.config import Settings


class AnalyticsService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.path = settings.project_root / "logs" / "events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log_event(self, event_type: str, **payload) -> None:
        event = {"type": event_type, "timestamp": datetime.now(UTC).isoformat(), **payload}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, sort_keys=True) + "\n")

    def read_events(self) -> list[dict]:
        if not self.path.exists():
            return []
        events = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return events

    def summarize(self, documents: int, chunks: int) -> dict:
        events = self.read_events()
        counts = Counter(str(event.get("type", "unknown")) for event in events)
        retrieval_latencies = _numbers(events, "retrieval_latency_ms")
        generation_latencies = _numbers(events, "generation_latency_ms")
        confidences = _numbers(events, "confidence")
        return {
            "documents": documents,
            "chunks": chunks,
            "queries": counts["retrieval"] + counts["chat"],
            "duplicate_files": counts["duplicate"],
            "rejected_files": counts["rejected"],
            "average_retrieval_latency_ms": round(mean(retrieval_latencies), 2) if retrieval_latencies else 0.0,
            "average_generation_latency_ms": round(mean(generation_latencies), 2) if generation_latencies else 0.0,
            "average_confidence": round(mean(confidences), 4) if confidences else 0.0,
            "embedding_model": self.settings.embedding_model,
            "retrievers": ["bm25", "vector", "hybrid"],
            "llm": self.settings.ollama_model,
            "events_by_type": dict(counts),
        }


def _numbers(events: list[dict], key: str) -> list[float]:
    values = []
    for event in events:
        value = event.get(key)
        if isinstance(value, int | float):
            values.append(float(value))
    return values
