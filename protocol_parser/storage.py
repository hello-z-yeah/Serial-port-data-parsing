"""Reliable asynchronous raw serial-data writer.

The writer owns its file handle and performs rotation inside the same worker
thread.  This avoids races between a GUI thread closing/replacing a file while
the background writer is still using it.  Queue overflow and disk failures are
observable instead of being silently ignored.
"""
from __future__ import annotations

import os
import glob
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .exceptions import StorageOperationError


@dataclass(frozen=True)
class RawWriterStats:
    queued: int
    written_records: int
    written_bytes: int
    dropped_records: int
    file_index: int
    current_path: str
    failed: bool


_STOP = object()


class RawDataWriter:
    """Bounded, drainable writer for timestamped RX/TX records."""

    def __init__(
        self,
        *,
        directory: str | Path,
        basename: str,
        max_file_bytes: int = 50 * 1024 * 1024,
        ascii_mode: bool = False,
        queue_size: int = 5000,
        batch_bytes: int = 64 * 1024,
        batch_interval: float = 0.1,
        on_error: Callable[[str], None] | None = None,
        on_drop: Callable[[int], None] | None = None,
        opener=None,
    ) -> None:
        self.directory = Path(directory)
        self.basename = self._sanitize_basename(basename)
        self.max_file_bytes = max(1024, int(max_file_bytes))
        self.ascii_mode = bool(ascii_mode)
        self.batch_bytes = max(1024, int(batch_bytes))
        self.batch_interval = max(0.01, float(batch_interval))
        self.on_error = on_error
        self.on_drop = on_drop
        self._opener = opener or open

        self._queue: queue.Queue = queue.Queue(maxsize=max(1, int(queue_size)))
        self._thread: threading.Thread | None = None
        self._accepting = False
        self._stop_requested = threading.Event()
        self._state_lock = threading.RLock()
        self._file = None
        self._current_path: Path | None = None
        self._current_size = 0
        self._file_index = 0
        self._written_records = 0
        self._written_bytes = 0
        self._dropped_records = 0
        self._failed = False
        self._last_error = ""

    @staticmethod
    def _sanitize_basename(value: str) -> str:
        name = str(value or "serial_data").strip() or "serial_data"
        for ch in '<>:"/\\|?*':
            name = name.replace(ch, "_")
        return name.rstrip(". ") or "serial_data"

    @property
    def current_path(self) -> Path | None:
        with self._state_lock:
            return self._current_path

    @property
    def last_error(self) -> str:
        with self._state_lock:
            return self._last_error

    @property
    def running(self) -> bool:
        thread = self._thread
        return bool(self._accepting and thread is not None and thread.is_alive())

    def stats(self) -> RawWriterStats:
        with self._state_lock:
            return RawWriterStats(
                queued=self._queue.qsize(),
                written_records=self._written_records,
                written_bytes=self._written_bytes,
                dropped_records=self._dropped_records,
                file_index=self._file_index,
                current_path=str(self._current_path or ""),
                failed=self._failed,
            )

    def start(self) -> Path:
        with self._state_lock:
            if self.running and self._current_path is not None:
                return self._current_path
            if self._thread is not None and self._thread.is_alive():
                raise StorageOperationError(
                    "上一条原始数据写入线程仍在停止中，不能启动第二个写入线程"
                )
            self.directory.mkdir(parents=True, exist_ok=True)
            self._stop_requested.clear()
            self._failed = False
            self._last_error = ""
            self._file_index = self._find_last_existing_index()
            self._written_records = 0
            self._written_bytes = 0
            self._dropped_records = 0
            self._drain_queue_without_callbacks()
            self._open_current_file(append=True)
            # If the last existing part is already full, begin with a fresh,
            # unused part instead of appending beyond the limit or overwriting
            # an older _NNN file from a previous session.
            if self._current_size >= self.max_file_bytes:
                self._close_file(sync=False)
                self._file_index = self._next_unused_index(self._file_index + 1)
                self._open_current_file(append=False)
            self._accepting = True
            self._thread = threading.Thread(
                target=self._run,
                name="smst-raw-writer",
                daemon=True,
            )
            try:
                self._thread.start()
            except RuntimeError as exc:
                self._accepting = False
                self._thread = None
                try:
                    self._close_file(sync=False)
                except Exception:
                    pass
                raise StorageOperationError(f"无法启动原始数据写入线程：{exc}") from exc
            assert self._current_path is not None
            return self._current_path

    def enqueue(self, data: bytes, ts: float, prefix: str = "") -> bool:
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError("raw writer data must be bytes")
        if not data:
            return True
        if not self._accepting:
            return False
        try:
            self._queue.put_nowait((float(ts), str(prefix), bytes(data)))
            return True
        except queue.Full:
            with self._state_lock:
                self._dropped_records += 1
                dropped = self._dropped_records
            # Notify on the first drop and periodically thereafter; do not flood
            # the GUI while a high-rate source remains above disk throughput.
            if self.on_drop is not None and (dropped == 1 or dropped % 100 == 0):
                try:
                    self.on_drop(dropped)
                except Exception:
                    pass
            return False

    def stop(self, *, drain: bool = True, timeout: float = 5.0) -> RawWriterStats:
        self._accepting = False
        self._stop_requested.set()
        if not drain:
            self._drain_queue_without_callbacks()
        try:
            self._queue.put_nowait(_STOP)
        except queue.Full:
            # The worker will observe stop_requested after draining one item.
            pass
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
            if thread.is_alive():
                raise StorageOperationError("原始数据写入线程未能在限定时间内停止")
        with self._state_lock:
            self._thread = None
        try:
            self._close_file(sync=True)
        except Exception as exc:
            self._fail(exc)
            raise StorageOperationError(f"关闭原始数据文件失败：{exc}") from exc
        return self.stats()

    def _drain_queue_without_callbacks(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except queue.Empty:
                return

    def _file_path(self, index: int) -> Path:
        suffix = "" if index == 0 else f"_{index:03d}"
        return self.directory / f"{self.basename}{suffix}.dat"

    def _find_last_existing_index(self) -> int:
        """Return the highest existing part index without trusting directory order."""
        highest = 0 if self._file_path(0).exists() else -1
        prefix = f"{self.basename}_"
        pattern = f"{glob.escape(self.basename)}_*.dat"
        for path in self.directory.glob(pattern):
            stem = path.stem
            if not stem.startswith(prefix):
                continue
            raw_index = stem[len(prefix):]
            if raw_index.isdigit():
                highest = max(highest, int(raw_index))
        return max(0, highest)

    def _next_unused_index(self, start: int) -> int:
        index = max(1, int(start))
        while self._file_path(index).exists():
            index += 1
        return index

    def _open_current_file(self, *, append: bool) -> None:
        path = self._file_path(self._file_index)
        mode = "ab" if append else "wb"
        try:
            fp = self._opener(path, mode)
        except Exception as exc:
            raise StorageOperationError(f"无法打开原始数据文件：{path}；原因：{exc}") from exc
        self._file = fp
        self._current_path = path
        try:
            self._current_size = path.stat().st_size if append else 0
        except OSError:
            self._current_size = 0

    def _close_file(self, *, sync: bool) -> None:
        fp = self._file
        self._file = None
        if fp is None:
            return
        try:
            fp.flush()
            if sync:
                try:
                    os.fsync(fp.fileno())
                except (OSError, AttributeError):
                    pass
        finally:
            fp.close()

    def _rotate(self) -> None:
        self._close_file(sync=True)
        self._file_index = self._next_unused_index(self._file_index + 1)
        self._open_current_file(append=False)

    def _format_record(self, ts: float, prefix: str, data: bytes) -> bytes:
        ts_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        if self.ascii_mode:
            text = data.decode("utf-8", errors="replace")
            lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            rendered = "".join(
                f"[{ts_str}] {prefix}{line}\n" for line in lines if line.strip()
            )
            if not rendered:
                rendered = f"[{ts_str}] {prefix}\n"
        else:
            rendered = f"[{ts_str}] {prefix}{' '.join(f'{b:02X}' for b in data)}\n"
        return rendered.encode("utf-8", errors="replace")

    def _fail(self, exc: BaseException) -> None:
        message = f"原始数据保存已停止：{exc}"
        with self._state_lock:
            self._failed = True
            self._last_error = message
        self._accepting = False
        self._stop_requested.set()
        if self.on_error is not None:
            try:
                self.on_error(message)
            except Exception:
                pass

    def _run(self) -> None:
        pending = bytearray()
        record_count = 0
        last_flush = time.monotonic()

        def flush_pending() -> None:
            nonlocal pending, record_count, last_flush
            if not pending:
                last_flush = time.monotonic()
                return
            fp = self._file
            if fp is None:
                raise StorageOperationError("原始数据文件句柄已关闭")
            view = memoryview(pending)
            total = 0
            while total < len(view):
                written = fp.write(view[total:])
                if written is None:
                    written = len(view) - total
                if written <= 0:
                    raise StorageOperationError("磁盘写入返回 0 字节")
                total += written
            fp.flush()
            self._current_size += total
            with self._state_lock:
                self._written_records += record_count
                self._written_bytes += total
            pending = bytearray()
            record_count = 0
            last_flush = time.monotonic()
            # Rotate only when the next record arrives.  Rotating immediately
            # after a final oversized batch creates an empty trailing _NNN file.

        try:
            while True:
                timeout = max(0.01, self.batch_interval - (time.monotonic() - last_flush))
                try:
                    item = self._queue.get(timeout=timeout)
                except queue.Empty:
                    item = None

                if item is _STOP:
                    self._queue.task_done()
                    if self._queue.empty():
                        break
                    continue
                if item is None:
                    if pending:
                        flush_pending()
                    if self._stop_requested.is_set() and self._queue.empty():
                        break
                    continue

                try:
                    ts, prefix, data = item
                    rendered = self._format_record(ts, prefix, data)
                    # Rotate before adding an oversized next record so normal
                    # files do not significantly exceed the configured limit.
                    if pending and self._current_size + len(pending) + len(rendered) > self.max_file_bytes:
                        flush_pending()
                    if self._current_size > 0 and self._current_size + len(rendered) > self.max_file_bytes:
                        self._rotate()
                    pending.extend(rendered)
                    record_count += 1
                finally:
                    self._queue.task_done()

                if len(pending) >= self.batch_bytes or time.monotonic() - last_flush >= self.batch_interval:
                    flush_pending()
                if self._stop_requested.is_set() and self._queue.empty():
                    break

            flush_pending()
            self._close_file(sync=True)
        except Exception as exc:
            try:
                self._close_file(sync=False)
            except Exception:
                pass
            self._fail(exc)
        finally:
            self._accepting = False
