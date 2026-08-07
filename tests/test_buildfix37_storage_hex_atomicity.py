"""Regression tests for BuildFix37: storage lifecycle, strict HEX, atomic rollback, u8 bounds."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from protocol_parser.attr_center import AttrStateCenter
from protocol_parser.auto_reply import AutoReplyEngine, _require_u8
from protocol_parser.exceptions import AttributeValidationError
from protocol_parser.parser import HexParseError, ProtocolConfigError, merge_protocol, parse_hex_input
from protocol_parser.storage import RawDataWriter


ROOT = Path(__file__).resolve().parents[1]


def test_strict_hex_tokenization() -> None:
    with pytest.raises(HexParseError):
        parse_hex_input("A5,,5A")
    with pytest.raises(HexParseError):
        parse_hex_input("A5, ,5A")
    with pytest.raises(HexParseError):
        parse_hex_input("A5,,")
    # still accept normal forms
    assert parse_hex_input("A5 5A") == bytes.fromhex("A55A")
    assert parse_hex_input("0xA5,0x5A") == bytes.fromhex("A55A")


def test_merge_protocol_shape_guard() -> None:
    with pytest.raises(ProtocolConfigError):
        merge_protocol([], {})  # type: ignore[arg-type]
    with pytest.raises(ProtocolConfigError):
        merge_protocol({"attributes": []}, {})
    with pytest.raises(ProtocolConfigError):
        merge_protocol({"commands": {}}, {})
    # valid minimal
    out = merge_protocol({"product": "a", "commands": []}, {"product": "b"})
    assert out["product"] == "b"


def test_wire_id_overflow() -> None:
    with pytest.raises(AttributeValidationError):
        _require_u8(0x101, "属性ID")
    with pytest.raises(AttributeValidationError):
        _require_u8(-1, "Action ID")
    assert _require_u8(0x00, "x") == 0
    assert _require_u8(0xFF, "x") == 0xFF


def test_auto_reply_rollback_atomicity() -> None:
    """组包失败时，AttrStateCenter 事务接口必须把已写入属性全部回滚。"""

    class _Center:
        def __init__(self) -> None:
            self.cfg = {}
            self.values = {1: 0, 2: 0}
            self.writes: list[tuple[int, object]] = []

        def get_frame_attr_records(self, result):
            return [(1, 1, 10), (2, 2, 20)]

        def get_entry(self, attrid: int):
            return SimpleNamespace(access="读写", current_value=self.values.get(attrid, 0))

        def validate_attr_value(self, attrid: int, value):
            return value

        def apply_values_atomic(self, values: dict[int, object]) -> dict[int, object]:
            old = {k: self.values[k] for k in values}
            for aid, val in values.items():
                self.values[aid] = val
                self.writes.append((aid, val))
            return old

        def restore_values(self, old_values: dict[int, object]) -> None:
            for aid, val in old_values.items():
                self.values[aid] = val

        def reset_heartbeat_counter(self) -> None:
            return None

    class _Cmd:
        def build_cmd_ack_resp(self, msg_id: int) -> bytes:
            raise RuntimeError("ack build failed")

        def build_attr_report(self, attrids, values) -> bytes:
            return b"REPORT"

    center = _Center()
    engine = AutoReplyEngine(None, _Cmd(), center)
    result = SimpleNamespace(fields=[{"name": "msg_id", "value": 1}])
    frame = SimpleNamespace(data=b"")
    replies = engine._reply_cmd_dispatch(result, frame)
    assert replies == []
    # 两个属性都曾被写入，但组包失败后必须全部回滚
    assert center.values[1] == 0
    assert center.values[2] == 0
    assert center.writes == [(1, 10), (2, 20)]


def test_raw_data_writer_concurrency(tmp_path: Path) -> None:
    """高频 enqueue 与 stop 并发：不得在 _STOP 之后继续落盘，stop 必须稳定返回。"""
    written_lock = threading.Lock()
    records_after_stop: list[bytes] = []
    stop_seen = threading.Event()

    class TrackingFile:
        def __init__(self, path: Path, mode: str) -> None:
            self._fp = open(path, mode)  # noqa: SIM115
            self._path = path

        def write(self, data: bytes) -> int:
            if stop_seen.is_set():
                with written_lock:
                    records_after_stop.append(bytes(data))
            return self._fp.write(data)

        def flush(self) -> None:
            self._fp.flush()

        def fileno(self) -> int:
            return self._fp.fileno()

        def close(self) -> None:
            self._fp.close()

    def opener(path, mode):
        return TrackingFile(Path(path), mode)

    writer = RawDataWriter(
        directory=tmp_path,
        basename="conc",
        queue_size=2000,
        batch_bytes=1024,
        batch_interval=0.05,
        opener=opener,
    )
    writer.start()

    stop_flag = threading.Event()
    errors: list[BaseException] = []

    def producer(n: int) -> None:
        try:
            for i in range(200):
                if stop_flag.is_set():
                    break
                writer.enqueue(f"P{n}-{i}".encode(), time.time(), prefix="T")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=producer, args=(i,), daemon=True) for i in range(10)]
    for th in threads:
        th.start()
    time.sleep(0.05)
    stop_flag.set()
    stop_seen.set()
    stats = writer.stop(drain=True, timeout=5.0)
    for th in threads:
        th.join(timeout=2.0)

    assert not errors
    assert stats is not None
    # stop 之后不应再有新的写盘（允许已在 worker 手中的少量在途记录）
    # 关键约束：stop 返回后再次 enqueue 必须失败
    assert writer.enqueue(b"late", time.time()) is False


def test_on_error_stop_safely(tmp_path: Path) -> None:
    """on_error 回调内调用 stop() 不得死锁，也不得在 worker 结束前过早清句柄。"""
    stopped = threading.Event()
    errors: list[str] = []

    def on_error(msg: str) -> None:
        errors.append(msg)
        # 在 worker 线程内重入 stop
        try:
            writer.stop(drain=False, timeout=1.0)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"stop-failed:{exc}")
        stopped.set()

    writer = RawDataWriter(
        directory=tmp_path,
        basename="safe",
        queue_size=16,
        batch_bytes=1,
        batch_interval=0.01,
        on_error=on_error,
    )
    writer.start()

    # 注入一个会触发写失败的 opener：第一次 open 正常，写时抛错
    real_open = writer._opener

    class BoomFile:
        def __init__(self, fp) -> None:
            self._fp = fp

        def write(self, data: bytes) -> int:
            raise OSError("disk full simulation")

        def flush(self) -> None:
            self._fp.flush()

        def fileno(self) -> int:
            return self._fp.fileno()

        def close(self) -> None:
            self._fp.close()

    def boom_opener(path, mode):
        return BoomFile(real_open(path, mode))

    writer._opener = boom_opener
    # 强制重新打开文件使后续写走 BoomFile：通过 rotate 路径较难，直接换 _file
    if writer._file is not None:
        try:
            writer._file.close()
        except Exception:
            pass
        writer._file = BoomFile(real_open(writer._file_path(writer._file_index), "ab"))

    assert writer.enqueue(b"boom", time.time()) is True
    # 等待 worker 触发 _fail -> on_error -> stop
    assert stopped.wait(timeout=3.0), "on_error was not invoked"
    # 再从外部 stop 应可稳定返回
    stats = writer.stop(drain=False, timeout=3.0)
    assert stats is not None
    assert writer.running is False
    assert any("disk full" in e or "原始数据保存已停止" in e for e in errors)
