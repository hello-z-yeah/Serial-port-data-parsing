from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from protocol_parser.exceptions import StorageOperationError
from protocol_parser.storage import RawDataWriter


def test_raw_writer_drains_all_records_and_counts_real_utf8_bytes(tmp_path: Path) -> None:
    writer = RawDataWriter(
        directory=tmp_path,
        basename="capture",
        ascii_mode=True,
        batch_interval=0.01,
    )
    path = writer.start()
    assert writer.enqueue("中文".encode("utf-8"), 1_700_000_000.123, "RX ")
    assert writer.enqueue(b"ok", 1_700_000_001.456, "TX ")
    stats = writer.stop(drain=True, timeout=2.0)

    payload = path.read_bytes()
    assert "中文".encode("utf-8") in payload
    assert b"TX ok" in payload
    assert stats.written_records == 2
    assert stats.written_bytes == len(payload)
    assert stats.dropped_records == 0
    assert not stats.failed


def test_raw_writer_rotates_without_creating_empty_trailing_file(tmp_path: Path) -> None:
    writer = RawDataWriter(
        directory=tmp_path,
        basename="rotate",
        max_file_bytes=1024,
        batch_interval=0.01,
    )
    writer.start()
    # Each 400-byte HEX record renders to more than 1 KiB.  Two records should
    # produce exactly two non-empty files, not a third empty file at shutdown.
    assert writer.enqueue(bytes(range(200)) * 2, time.time())
    assert writer.enqueue(bytes(range(200)) * 2, time.time())
    stats = writer.stop(drain=True, timeout=2.0)

    files = sorted(tmp_path.glob("rotate*.dat"))
    assert len(files) == 2
    assert all(path.stat().st_size > 0 for path in files)
    assert stats.file_index == 1
    assert stats.written_records == 2


class BlockingFile:
    def __init__(self, started: threading.Event, release: threading.Event):
        self.started = started
        self.release = release
        self.closed = False
        self.buffer = bytearray()

    def write(self, data) -> int:
        self.started.set()
        self.release.wait(timeout=2.0)
        chunk = bytes(data)
        self.buffer.extend(chunk)
        return len(chunk)

    def flush(self) -> None:
        return None

    def fileno(self) -> int:
        raise OSError("no descriptor")

    def close(self) -> None:
        self.closed = True


def test_queue_overflow_is_counted_and_reported(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()
    dropped_events: list[int] = []
    file_obj = BlockingFile(started, release)
    writer = RawDataWriter(
        directory=tmp_path,
        basename="slow",
        queue_size=1,
        batch_interval=0.01,
        on_drop=dropped_events.append,
        opener=lambda _path, _mode: file_obj,
    )
    writer.start()
    assert writer.enqueue(b"first", time.time())
    assert started.wait(1.0)
    assert writer.enqueue(b"second", time.time())
    assert not writer.enqueue(b"third", time.time())
    assert dropped_events == [1]
    release.set()
    stats = writer.stop(drain=True, timeout=2.0)
    assert stats.dropped_records == 1
    assert stats.written_records == 2


class FailingFile:
    def write(self, _data) -> int:
        raise OSError("disk full")

    def flush(self) -> None:
        return None

    def fileno(self) -> int:
        raise OSError("no descriptor")

    def close(self) -> None:
        return None


def test_disk_write_failure_is_visible_and_marks_writer_failed(tmp_path: Path) -> None:
    error_event = threading.Event()
    errors: list[str] = []

    def on_error(message: str) -> None:
        errors.append(message)
        error_event.set()

    writer = RawDataWriter(
        directory=tmp_path,
        basename="failure",
        batch_interval=0.01,
        on_error=on_error,
        opener=lambda _path, _mode: FailingFile(),
    )
    writer.start()
    assert writer.enqueue(b"payload", time.time())
    assert error_event.wait(1.0)
    stats = writer.stop(drain=False, timeout=2.0)
    assert stats.failed
    assert "disk full" in writer.last_error
    assert errors and "原始数据保存已停止" in errors[0]


def test_open_failure_raises_storage_domain_error(tmp_path: Path) -> None:
    def fail_open(_path, _mode):
        raise PermissionError("read only")

    writer = RawDataWriter(
        directory=tmp_path,
        basename="nope",
        opener=fail_open,
    )
    with pytest.raises(StorageOperationError, match="无法打开原始数据文件"):
        writer.start()


def test_existing_rotated_files_are_never_overwritten(tmp_path: Path) -> None:
    base = tmp_path / "preserve.dat"
    part1 = tmp_path / "preserve_001.dat"
    base.write_bytes(b"A" * 1024)
    part1.write_bytes(b"B" * 1024)

    writer = RawDataWriter(
        directory=tmp_path,
        basename="preserve",
        max_file_bytes=1024,
        batch_interval=0.01,
    )
    path = writer.start()
    assert path == tmp_path / "preserve_002.dat"
    assert writer.enqueue(b"new", time.time())
    stats = writer.stop(drain=True, timeout=2.0)

    assert base.read_bytes() == b"A" * 1024
    assert part1.read_bytes() == b"B" * 1024
    assert (tmp_path / "preserve_002.dat").stat().st_size > 0
    assert stats.file_index == 2
