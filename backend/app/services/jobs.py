from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Event, Lock
from typing import Any, Callable

from app.core.cancellation import OperationCancelled, operations
from app.core.errors import AppError


ProgressCallback = Callable[[str, int, int], None]
JobWork = Callable[[Event, ProgressCallback], dict[str, Any]]


@dataclass
class IngestionJob:
    id: str
    kind: str
    cancel_event: Event
    status: str = "queued"
    phase: str = "Queued"
    completed: int = 0
    total: int = 0
    result: dict[str, Any] | None = None
    error: dict[str, str] | None = None
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    finished_at: str | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status,
            "phase": self.phase,
            "completed": self.completed,
            "total": self.total,
            "result": self.result,
            "error": self.error,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class IngestionJobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, IngestionJob] = {}
        self._lock = Lock()

    def submit(self, kind: str, work: JobWork) -> IngestionJob:
        job_id, cancel_event = operations.create()
        job = IngestionJob(id=job_id, kind=kind, cancel_event=cancel_event)
        with self._lock:
            self._jobs[job_id] = job
        asyncio.create_task(self._run(job, work))
        return job

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.snapshot() if job else None

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            if job.status in {"completed", "failed", "cancelled"}:
                return job.snapshot()
            job.status = "cancelling"
            job.phase = "Cancelling"
        operations.cancel(job_id)
        return self.get(job_id)

    async def _run(self, job: IngestionJob, work: JobWork) -> None:
        self._update(job, status="running", phase="Preparing")
        try:
            result = await asyncio.to_thread(work, job.cancel_event, lambda phase, completed, total: self._progress(job, phase, completed, total))
        except OperationCancelled:
            self._update(job, status="cancelled", phase="Cancelled")
        except AppError as exc:
            self._update(job, status="failed", phase="Failed", error={"code": exc.code, "message": exc.message})
        except Exception:
            self._update(job, status="failed", phase="Failed", error={"code": "ingestion_failed", "message": "Ingestion failed."})
        else:
            self._update(job, status="completed", phase="Completed", result=result)
        finally:
            with self._lock:
                job.finished_at = datetime.now(UTC).isoformat()
            operations.release(job.id)

    def _progress(self, job: IngestionJob, phase: str, completed: int, total: int) -> None:
        self._update(job, phase=phase, completed=completed, total=total)

    def _update(self, job: IngestionJob, **changes: Any) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(job, key, value)


ingestion_jobs = IngestionJobManager()
