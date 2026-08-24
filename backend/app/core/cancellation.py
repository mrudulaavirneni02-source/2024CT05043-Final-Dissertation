from __future__ import annotations

from threading import Event, Lock
from uuid import uuid4

from app.core.errors import AppError


class OperationCancelled(AppError):
    def __init__(self) -> None:
        super().__init__("Operation cancelled.", status_code=409, code="operation_cancelled")


class OperationRegistry:
    def __init__(self) -> None:
        self._events: dict[str, Event] = {}
        self._lock = Lock()

    def create(self) -> tuple[str, Event]:
        operation_id = uuid4().hex
        return operation_id, self.register(operation_id)

    def register(self, operation_id: str) -> Event:
        with self._lock:
            return self._events.setdefault(operation_id, Event())

    def cancel(self, operation_id: str) -> bool:
        with self._lock:
            event = self._events.get(operation_id)
        if event is None:
            return False
        event.set()
        return True

    def release(self, operation_id: str) -> None:
        with self._lock:
            self._events.pop(operation_id, None)


operations = OperationRegistry()


def raise_if_cancelled(cancel_event: Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise OperationCancelled()
