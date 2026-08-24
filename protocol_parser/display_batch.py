"""Thread-safe display batching for parse/worker threads."""
from __future__ import annotations

import threading
import time
from typing import Any, Callable


class DisplayBatcher:
    """Coalesce display payloads and emit them every ``batch_ms``."""

    def __init__(
        self,
        emit: Callable[[dict[str, Any]], None],
        *,
        batch_ms: float = 40.0,
        max_items: int = 200,
    ) -> None:
        self._emit = emit
        self._batch_ms = float(batch_ms)
        self._max_items = max(1, int(max_items))
        self._lock = threading.Lock()
        self._items: list[dict[str, Any]] = []
        self._frame_count = 0
        self._last_flush = time.time()

    def add(self, item: dict[str, Any], *, frame: bool = False) -> None:
        with self._lock:
            self._items.append(item)
            if frame:
                self._frame_count += 1
            self._maybe_flush_locked()

    def extend(self, items: list[dict[str, Any]], *, frame_count: int = 0) -> None:
        if not items and frame_count <= 0:
            return
        with self._lock:
            self._items.extend(items)
            self._frame_count += max(0, int(frame_count))
            self._maybe_flush_locked()

    def flush(self, *, force: bool = False) -> None:
        with self._lock:
            self._maybe_flush_locked(force=force)

    def _maybe_flush_locked(self, *, force: bool = False) -> None:
        if not self._items and self._frame_count <= 0:
            return
        now = time.time()
        if (
            not force
            and len(self._items) < self._max_items
            and (now - self._last_flush) * 1000.0 < self._batch_ms
        ):
            return
        payload = {
            "lines": self._items,
            "frame_count": self._frame_count,
        }
        self._items = []
        self._frame_count = 0
        self._last_flush = now
        self._emit(payload)


class SegmentBatchAccumulator:
    """Merge MCU display segment lists before one GUI signal."""

    def __init__(
        self,
        emit: Callable[[list[list[tuple]]], None],
        *,
        batch_ms: float = 40.0,
        max_batches: int = 48,
    ) -> None:
        self._emit = emit
        self._batch_ms = float(batch_ms)
        self._max_batches = max(1, int(max_batches))
        self._lock = threading.Lock()
        self._pending: list[list[tuple]] = []
        self._last_flush = time.time()

    def add(self, segments: list[tuple]) -> None:
        if not segments:
            return
        with self._lock:
            self._pending.append(segments)
            self._maybe_flush_locked()

    def flush(self, *, force: bool = False) -> None:
        with self._lock:
            self._maybe_flush_locked(force=force)

    def _maybe_flush_locked(self, *, force: bool = False) -> None:
        if not self._pending:
            return
        now = time.time()
        if (
            not force
            and len(self._pending) < self._max_batches
            and (now - self._last_flush) * 1000.0 < self._batch_ms
        ):
            return
        batches = self._pending
        self._pending = []
        self._last_flush = now
        self._emit(batches)
