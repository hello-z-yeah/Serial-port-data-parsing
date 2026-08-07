from __future__ import annotations

import queue
import threading
import time
from types import SimpleNamespace

import pytest

import protocol_parser.serial_collector as sc
from protocol_parser.exceptions import TxQueueFullError
from protocol_parser.serial_collector import SerialCollector, TxRequest


class FakeSerialException(OSError):
    pass


class FakeSerialPort:
    def __init__(self, *args, read_chunks=None, write_gate=None, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.is_open = True
        self._read_chunks = queue.Queue()
        for chunk in read_chunks or []:
            self._read_chunks.put(bytes(chunk))
        self.write_gate = write_gate
        self.write_started = threading.Event()
        self.writes: list[tuple[bytes, str]] = []
        self.cancel_read_called = False
        self.cancel_write_called = False

    def read(self, _size: int) -> bytes:
        if not self.is_open:
            return b""
        try:
            return self._read_chunks.get_nowait()
        except queue.Empty:
            time.sleep(0.005)
            return b""

    def write(self, payload: bytes) -> int:
        self.write_started.set()
        if self.write_gate is not None:
            self.write_gate.wait(timeout=2.0)
        if not self.is_open:
            raise FakeSerialException("closed")
        self.writes.append((bytes(payload), threading.current_thread().name))
        return len(payload)

    def close(self) -> None:
        self.is_open = False

    def cancel_read(self) -> None:
        self.cancel_read_called = True

    def cancel_write(self) -> None:
        self.cancel_write_called = True
        if self.write_gate is not None:
            self.write_gate.set()


class FakeSerialModule:
    FIVEBITS = 5
    SIXBITS = 6
    SEVENBITS = 7
    EIGHTBITS = 8
    STOPBITS_ONE = 1.0
    STOPBITS_ONE_POINT_FIVE = 1.5
    STOPBITS_TWO = 2.0
    PARITY_NONE = "N"
    SerialException = FakeSerialException

    def __init__(self, factory):
        self._factory = factory
        self.tools = SimpleNamespace(list_ports=SimpleNamespace(comports=lambda: []))

    def Serial(self, *args, **kwargs):
        return self._factory(*args, **kwargs)


def install_fake_serial(monkeypatch, *, read_chunks=None, write_gate=None):
    created: list[FakeSerialPort] = []

    def factory(*args, **kwargs):
        port = FakeSerialPort(
            *args,
            read_chunks=read_chunks,
            write_gate=write_gate,
            **kwargs,
        )
        created.append(port)
        return port

    monkeypatch.setattr(sc, "serial", FakeSerialModule(factory), raising=False)
    monkeypatch.setattr(sc, "HAS_SERIAL", True)
    return created


def test_send_only_enqueues_and_tx_worker_reports_metadata(monkeypatch) -> None:
    gate = threading.Event()
    created = install_fake_serial(monkeypatch, write_gate=gate)
    callback_event = threading.Event()
    callback_values = []

    collector = SerialCollector(
        cfg={},
        port="COM_TEST",
        primary_enabled=False,
        on_tx_sent=lambda payload, label, ts, metadata: (
            callback_values.append((payload, label, ts, metadata)),
            callback_event.set(),
        ),
    )
    collector.start()
    try:
        started = time.perf_counter()
        assert collector.send(b"\xA5\x5A", metadata={"auto_reply": True}) == 2
        elapsed = time.perf_counter() - started
        assert elapsed < 0.1
        assert created[0].write_started.wait(1.0)
        # The driver is still blocked, proving send() did not perform the write.
        assert not callback_event.is_set()
        gate.set()
        assert callback_event.wait(1.0)
        assert created[0].writes == [(b"\xA5\x5A", "smst-tx-COM_TEST")]
        payload, label, _ts, metadata = callback_values[0]
        assert payload == b"\xA5\x5A"
        assert label == "TX"
        assert metadata == {"auto_reply": True}
    finally:
        gate.set()
        collector.stop(timeout=2.0)


def test_tx_queue_full_is_an_explicit_domain_error() -> None:
    collector = SerialCollector(cfg={}, port="COM_TEST", tx_queue_size=1)
    collector.running = True
    collector._serial = SimpleNamespace(is_open=True)
    collector._tx_queue = queue.Queue(maxsize=1)
    collector._tx_queue.put_nowait(TxRequest(b"first"))
    with pytest.raises(TxQueueFullError):
        collector.send(b"second")


def test_stop_async_returns_immediately_and_finishes(monkeypatch) -> None:
    created = install_fake_serial(monkeypatch)
    collector = SerialCollector(cfg={}, port="COM_STOP", primary_enabled=False)
    collector.start()
    completed = threading.Event()
    errors: list[BaseException] = []

    started = time.perf_counter()
    stop_thread = collector.stop_async(
        timeout=2.0,
        on_complete=completed.set,
        on_error=errors.append,
    )
    elapsed = time.perf_counter() - started

    assert elapsed < 0.1
    assert stop_thread is not threading.current_thread()
    assert completed.wait(2.0)
    stop_thread.join(1.0)
    assert not errors
    assert not collector.running
    assert collector._serial is None
    assert created[0].cancel_read_called
    assert created[0].cancel_write_called


def test_mcu_mode_does_not_forward_to_primary_channel(monkeypatch) -> None:
    install_fake_serial(monkeypatch, read_chunks=[b"\x01\x02\x03"])
    mcu_event = threading.Event()
    mcu_raw: list[bytes] = []
    primary_raw: list[bytes] = []

    collector = SerialCollector(
        cfg={},
        port="COM_MCU",
        primary_enabled=False,
        mcu_cfg={},
        on_mcu_frame=lambda _result, frame, _ts: (
            mcu_raw.append(frame.raw),
            mcu_event.set(),
        ),
        on_raw=lambda data, _ts: primary_raw.append(data),
    )
    collector.start()
    try:
        assert mcu_event.wait(1.0)
        assert mcu_raw == [b"\x01\x02\x03"]
        assert primary_raw == []
    finally:
        collector.stop(timeout=2.0)


def test_receive_analysis_mode_has_no_hidden_mcu_channel(monkeypatch) -> None:
    install_fake_serial(monkeypatch, read_chunks=[b"abc"])
    primary_event = threading.Event()
    primary_raw: list[bytes] = []
    mcu_calls: list[bytes] = []

    collector = SerialCollector(
        cfg={},
        port="COM_RX",
        primary_enabled=True,
        on_mcu_frame=None,
        mcu_cfg={},
        raw_mode=True,
        raw_batch_bytes=1,
        on_raw=lambda data, _ts: (primary_raw.append(data), primary_event.set()),
    )
    # Even if a caller later records a stale MCU callback elsewhere, this
    # collector instance has no MCU callback/synchronizer and cannot dispatch it.
    assert collector.on_mcu_frame is None
    collector.start()
    try:
        assert primary_event.wait(1.0)
        assert primary_raw == [b"abc"]
        assert mcu_calls == []
        assert collector.mcu_sync is None
    finally:
        collector.stop(timeout=2.0)
