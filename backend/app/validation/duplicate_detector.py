import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from app.core.cancellation import raise_if_cancelled
from app.core.config import Settings


class DuplicateDetector:
    def __init__(self, settings: Settings) -> None:
        self.path = settings.project_root / "storage" / "document_hashes.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def sha256(self, path: Path, *, cancel_event: Event | None = None) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                raise_if_cancelled(cancel_event)
                digest.update(block)
        return digest.hexdigest()

    def load(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def already_indexed(self, file_hash: str) -> bool:
        return file_hash in self.load()

    def register(self, file_hash: str, filename: str, chunks_indexed: int) -> None:
        data = self.load()
        data[file_hash] = {
            "filename": filename,
            "chunks_indexed": chunks_indexed,
            "indexed_at": datetime.now(UTC).isoformat(),
        }
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
