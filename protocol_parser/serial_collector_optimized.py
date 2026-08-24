"""Optimized serial collector with robust lifecycle management.

Features:
- Short read timeout (0.1s) for responsive stop — no select.select
- Non-blocking read loop via direct serial.read(4096)
- Guaranteed serial port close on stop (ownership-clear)
- Background-thread reconnect — never called from RX thread's own stack
- Public API: is_running(), is_connected(), get_error_count(), send_data()
"""
from __future__ import annotations

import inspect
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .exceptions import SerialOperationError, SerialStateError, TxQueueFullError
from .serial_collector import (
    FrameSynchronizer,
    _classify_serial_error,
    _friendly_serial_error,
)
from .parser import Frame, ParseResult, parse_frame

try:
    import serial
    HAS_SERIAL = True
    SerialTimeoutException = serial.SerialTimeoutException
except ImportError:
    HAS_SERIAL = False

    class SerialTimeoutException(Exception):
        pass


@dataclass
class OptimizedSerialCollector:
    cfg: dict
    port: str
    baudrate: int = 9600
    bytesize: int = 8
    stopbits: float = 1.0
    direction: str | None = None
    on_frame: Callable[[ParseResult, Frame, float], None] | None = None
    on_error: Callable[[str], None] | None = None
    on_connection_error: Callable[..., None] | None = None
    on_raw: Callable[[bytes, float], None] | None = None
    mcu_cfg: dict | None = None
    mcu_direction: str | None = "request"
    on_mcu_frame: Callable[[ParseResult | None, Frame, float], None] | None = None
    primary_enabled: bool = True
    raw_batch_bytes: int = 2048
    raw_batch_ms: float = 40.0
    raw_mode: bool = False
    on_tx_sent: Callable[..., None] | None = None
    tx_queue_size: int = 1000
    parse_queue_size: int = 512
    max_tx_payload_bytes: int = 1024 * 1024
    max_reconnect_attempts: int = 5
    reconnect_delay: float = 1.0

    running: bool = False
    _thread: threading.Thread | None = None
    _tx_thread: threading.Thread | None = None
    _parse_thread: threading.Thread | None = None
    _reconnect_thread: threading.Thread | None = None
    _serial: "serial.Serial | None" = None
    sync: FrameSynchronizer | None = None
    mcu_sync: FrameSynchronizer | None = None
    _write_lock: threading.Lock | None = None
    _mcu_sync_lock: threading.Lock = field(default_factory=threading.Lock)
    _tx_queue: queue.Queue | None = None
    _parse_queue: queue.Queue | None = None
    _stop_event: threading.Event = field(default_factory=threading.Event)
    _state_lock: threading.RLock = field(default_factory=threading.RLock)
    _stopping: bool = False
    tx_dropped_on_stop: int = 0
    rx_parse_overflows: int = 0
    last_connection_error_kind: str = ""
    last_connection_error: str = ""
    _error_count: int = 0
    _connected: bool = False
    _generation: int = 0  # bumped on each start/reconnect to detect stale workers

    def is_running(self) -> bool:
        return self.running

    def is_connected(self) -> bool:
        with self._state_lock:
            return self._connected

    def get_error_count(self) -> int:
        return self._error_count

    def is_safely_stopped(self) -> bool:
        """Return True if this collector can be safely replaced.

        Checks that all background threads (RX, TX, reconnect) are
        fully dead and the serial handle is closed. This prevents
        resource leaks and locked COM ports when ``register_port``
        overwrites an existing entry during a failing stop/reconnect
        phase.
        """
        with self._state_lock:
            if self.running or self._connected or self._stopping:
                return False
            threads_alive = any(
                t is not None and t.is_alive()
                for t in (
                    self._thread,
                    self._tx_thread,
                    self._parse_thread,
                    self._reconnect_thread,
                )
            )
            if threads_alive:
                return False
            ser = self._serial
            if ser is not None and getattr(ser, "is_open", False):
                return False
            return True

    def start(self) -> None:
        if not HAS_SERIAL:
            raise SerialStateError("pyserial 未安装，请执行: pip install pyserial")
        with self._state_lock:
            if self.running:
                return
            if self._stopping:
                raise SerialStateError("串口正在停止中，请稍候再启动")
            if self._thread is not None and self._thread.is_alive():
                raise SerialStateError("上一条串口读取线程仍在停止中，请稍后重试")
            if self._tx_thread is not None and self._tx_thread.is_alive():
                raise SerialStateError("上一条串口发送线程仍在停止中，请稍后重试")

            self.sync = FrameSynchronizer(self.cfg or {})
            self.set_mcu_cfg(self.mcu_cfg if self.on_mcu_frame is not None else {})
            self._serial = self._open_serial()
            self._write_lock = self._write_lock or threading.Lock()
            self._tx_queue = queue.Queue(maxsize=max(1, int(self.tx_queue_size)))
            self._parse_queue = queue.Queue(
                maxsize=max(1, int(self.parse_queue_size))
            )
            self._stop_event.clear()
            self.tx_dropped_on_stop = 0
            self.rx_parse_overflows = 0
            self.last_connection_error_kind = ""
            self.last_connection_error = ""
            self._error_count = 0
            self._connected = True
            self.running = True
            self._generation += 1
            gen = self._generation
            self._tx_thread = threading.Thread(
                target=self._tx_loop,
                args=(gen,),
                daemon=True,
                name=f"smst-tx-{self.port}",
            )
            self._parse_thread = threading.Thread(
                target=self._parse_loop,
                args=(gen,),
                daemon=True,
                name=f"smst-parse-{self.port}",
            )
            self._thread = threading.Thread(
                target=self._read_loop_optimized,
                args=(gen,),
                daemon=True,
                name=f"smst-rx-{self.port}",
            )
            self._tx_thread.start()
            self._parse_thread.start()
            self._thread.start()

    def _open_serial(self) -> "serial.Serial":
        """Open serial port with configured parameters. Returns the serial object."""
        bytesize_map = {
            5: serial.FIVEBITS,
            6: serial.SIXBITS,
            7: serial.SEVENBITS,
            8: serial.EIGHTBITS,
        }
        stopbits_map = {
            1.0: serial.STOPBITS_ONE,
            1.5: serial.STOPBITS_ONE_POINT_FIVE,
            2.0: serial.STOPBITS_TWO,
        }
        try:
            sb_val = stopbits_map.get(float(self.stopbits), serial.STOPBITS_ONE)
        except (TypeError, ValueError):
            sb_val = serial.STOPBITS_ONE
        try:
            ser = serial.Serial(
                port=self.port,
                baudrate=self.baudrate,
                bytesize=bytesize_map.get(self.bytesize, serial.EIGHTBITS),
                parity=serial.PARITY_NONE,
                stopbits=sb_val,
                timeout=0.1,
                write_timeout=1.0,
            )
        except (serial.SerialException, OSError) as exc:
            kind = _classify_serial_error(exc)
            error = SerialOperationError(_friendly_serial_error(self.port, exc, kind))
            setattr(error, "kind", kind)
            raise error from exc
        return ser

    def _close_serial_safe(self, ser: "serial.Serial | None") -> None:
        """Idempotent, safe close of a serial port handle."""
        if ser is None:
            return
        try:
            if getattr(ser, "is_open", False):
                ser.close()
        except (OSError, AttributeError):
            pass

    def request_stop(self) -> None:
        """Signal both workers and cancel any pending OS-level read/write."""
        with self._state_lock:
            self._stopping = True
            self.running = False
            self._connected = False
            self._stop_event.set()
            serial_obj = self._serial
            tx_queue = self._tx_queue
        if serial_obj is not None:
            for method_name in ("cancel_read", "cancel_write"):
                try:
                    method = getattr(serial_obj, method_name, None)
                    if not callable(method):
                        continue
                    # pyserial win32 cancel_* uses ctypes.byref(overlapped). When no
                    # read/write is in flight the overlapped handle is None and
                    # raises TypeError — common when stopping for baud changes.
                    if method_name == "cancel_read":
                        if getattr(serial_obj, "_overlapped_read", None) is None:
                            continue
                    elif method_name == "cancel_write":
                        if getattr(serial_obj, "_overlapped_write", None) is None:
                            continue
                    method()
                except (OSError, AttributeError, TypeError, ValueError):
                    pass
        if tx_queue is not None:
            try:
                tx_queue.put_nowait(_TX_STOP)
            except queue.Full:
                pass

    def stop(self, *, timeout: float = 2.5) -> None:
        """Stop workers. Must not be called from within a worker thread."""
        self.request_stop()
        with self._state_lock:
            serial_obj = self._serial
            rx_thread = self._thread
            tx_thread = self._tx_thread
            parse_thread = self._parse_thread
            tx_queue = self._tx_queue
            parse_queue = self._parse_queue
            reconnect_thread = self._reconnect_thread

        # Close after cancellation — some Windows USB drivers unblock only on close.
        self._close_serial_safe(serial_obj)

        current = threading.current_thread()
        deadline = time.monotonic() + max(0.0, float(timeout))

        for worker in (rx_thread, parse_thread, tx_thread, reconnect_thread):
            if worker is None or worker is current:
                continue
            remaining = max(0.0, deadline - time.monotonic())
            if remaining <= 0:
                break
            worker.join(timeout=remaining)

        alive = [
            worker.name
            for worker in (rx_thread, parse_thread, tx_thread, reconnect_thread)
            if worker is not None and worker is not current and worker.is_alive()
        ]
        if alive:
            with self._state_lock:
                self._stopping = False
            raise SerialOperationError(
                f"串口线程未能及时停止：{', '.join(alive)}"
            )

        # Drain remaining TX queue items (balanced task_done).
        with self._state_lock:
            dropped = 0
            if tx_queue is not None:
                while True:
                    try:
                        item = tx_queue.get_nowait()
                    except queue.Empty:
                        break
                    else:
                        try:
                            if isinstance(item, TxRequest):
                                dropped += 1
                        finally:
                            try:
                                tx_queue.task_done()
                            except ValueError:
                                pass
            # Drain parse queue — no task_done accounting for it (simple Queue).
            if parse_queue is not None:
                while True:
                    try:
                        parse_queue.get_nowait()
                    except queue.Empty:
                        break
            self.tx_dropped_on_stop += dropped
            self._thread = None
            self._tx_thread = None
            self._parse_thread = None
            self._reconnect_thread = None
            self._serial = None
            self._tx_queue = None
            self._parse_queue = None
            if self.sync is not None:
                self.sync.reset()
            with self._mcu_sync_lock:
                if self.mcu_sync is not None:
                    self.mcu_sync.reset()
            self._stopping = False
            self.running = False
            self._connected = False
        if dropped:
            self._notify_error(f"串口停止时丢弃了 {dropped} 条尚未发送的 TX 请求")

    def stop_async(
        self,
        *,
        timeout: float = 2.5,
        on_complete: Callable[[], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> threading.Thread:
        """Compatibility API used by the GUI; stop without blocking its thread."""
        self.request_stop()

        def worker() -> None:
            try:
                self.stop(timeout=timeout)
            except BaseException as exc:
                if on_error is not None:
                    try:
                        on_error(exc)
                    except Exception:
                        pass
            else:
                if on_complete is not None:
                    try:
                        on_complete()
                    except Exception:
                        pass

        thread = threading.Thread(
            target=worker,
            daemon=True,
            name=f"smst-stop-{self.port}",
        )
        thread.start()
        return thread

    def _notify_error(self, message: str) -> None:
        callback = self.on_error
        if callback is None:
            return
        try:
            callback(message)
        except Exception as exc:
            try:
                from .paths import write_crash_log

                write_crash_log(exc)
            except Exception:
                pass

    def _notify_connection_error(
        self, message: str, kind: str | None = None
    ) -> None:
        resolved_kind = kind or _classify_serial_error(
            SerialOperationError(message)
        )
        self.last_connection_error_kind = resolved_kind
        self.last_connection_error = message
        callback = self.on_connection_error or self.on_error
        if callback is None:
            return
        try:
            try:
                parameter_count = len(inspect.signature(callback).parameters)
            except (TypeError, ValueError):
                parameter_count = 2
            if parameter_count >= 2:
                callback(message, resolved_kind)
            else:
                callback(message)
        except Exception as exc:
            try:
                from .paths import write_crash_log

                write_crash_log(exc)
            except Exception:
                pass

    def set_mcu_cfg(self, cfg: dict | None) -> None:
        resolved = cfg or {}
        frame_cfg = resolved.get("frame", {}) if isinstance(resolved, dict) else {}
        with self._mcu_sync_lock:
            self.mcu_cfg = resolved
            self.mcu_sync = (
                FrameSynchronizer(resolved) if frame_cfg else None
            )

    def _dispatch_mcu_frames(self, raw: bytes, ts: float) -> None:
        callback = self.on_mcu_frame
        if callback is None:
            return
        with self._mcu_sync_lock:
            sync = self.mcu_sync
            cfg = self.mcu_cfg or {}
            try:
                frames = sync.feed(raw) if sync is not None else []
            except Exception as exc:
                self._notify_error(
                    f"模拟MCU辅助通道组帧异常（主串口继续运行）: {exc}"
                )
                return
        if frames:
            for frame in frames:
                try:
                    result = parse_frame(
                        frame.raw, cfg, direction=self.mcu_direction
                    )
                except Exception:
                    result = None
                try:
                    callback(result, frame, ts)
                except Exception as exc:
                    self._notify_error(
                        f"模拟MCU协议回调异常（已跳过）: {exc}"
                    )
            return

        dummy = Frame(
            raw=raw,
            header=0,
            ver=0,
            cmd_code=0,
            length=0,
            data=b"",
            checksum_ok=None,
            checksum_expected=None,
            checksum_actual=None,
        )
        try:
            callback(None, dummy, ts)
        except Exception as exc:
            self._notify_error(
                f"模拟MCU原始数据回调异常（已跳过）: {exc}"
            )

    @staticmethod
    def _normalize_hex_payload(
        frame_bytes: bytes | bytearray | str,
    ) -> bytes:
        from .parser import EncodeFrameError

        if isinstance(frame_bytes, (bytes, bytearray)):
            return bytes(frame_bytes)
        if not isinstance(frame_bytes, str):
            raise TypeError("send_data() 需要 bytes 或 HEX 字符串")
        text = frame_bytes.strip()
        clean = (
            text.replace(" ", "")
            .replace("\n", "")
            .replace("\r", "")
            .replace("\t", "")
        )
        if clean.lower().startswith("0x"):
            clean = clean[2:]
        if len(clean) % 2:
            raise EncodeFrameError(
                f"TX HEX 字符数必须为偶数：{frame_bytes!r}"
            )
        try:
            return bytes.fromhex(clean)
        except ValueError as exc:
            raise EncodeFrameError(
                f"TX HEX 字符串非法：{frame_bytes!r}，原因：{exc}"
            ) from exc

    def _enqueue_tx(
        self,
        payload: bytes,
        label: str,
        metadata: dict[str, Any] | None,
    ) -> int:
        if not payload:
            return 0
        max_payload = max(1, int(self.max_tx_payload_bytes))
        if len(payload) > max_payload:
            raise SerialOperationError(
                f"单次发送数据不能超过 {max_payload} 字节，当前为 {len(payload)} 字节"
            )
        with self._state_lock:
            if (
                not self.running
                or self._serial is None
                or not getattr(self._serial, "is_open", False)
            ):
                raise SerialStateError("串口未打开，请先开始监控再发送")
            tx_queue = self._tx_queue
        if tx_queue is None:
            raise SerialStateError("串口发送线程尚未启动")
        request = TxRequest(bytes(payload), str(label), dict(metadata or {}))
        try:
            tx_queue.put_nowait(request)
        except queue.Full as exc:
            raise TxQueueFullError(
                "发送队列已满，请降低发送频率或等待队列处理完成"
            ) from exc
        return len(payload)

    def send_data(
        self,
        data: bytes | bytearray | str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        if isinstance(data, (bytes, bytearray)):
            payload = bytes(data)
        elif isinstance(data, str):
            stripped = data.strip()
            hex_chars = set("0123456789abcdefABCDEF \t\n\r")
            looks_like_hex = bool(stripped) and all(
                c in hex_chars for c in stripped
            )
            payload = (
                self._normalize_hex_payload(data)
                if looks_like_hex
                else data.encode("utf-8")
            )
        else:
            raise TypeError("send_data() 需要 bytes 或 str")
        return self._enqueue_tx(payload, "TX", metadata)

    def send(
        self,
        frame_bytes: bytes | bytearray | str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Primary collector-compatible HEX/bytes send API."""
        return self._enqueue_tx(
            self._normalize_hex_payload(frame_bytes),
            "TX",
            metadata,
        )

    def send_raw(
        self,
        data,
        *,
        as_text: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """Primary collector-compatible raw text/bytes send API."""
        if isinstance(data, (bytes, bytearray)):
            payload = bytes(data)
        elif isinstance(data, str):
            if as_text is None:
                stripped = data.strip()
                hex_chars = set("0123456789abcdefABCDEF \t\n\r")
                looks_like_hex = bool(stripped) and all(
                    character in hex_chars for character in stripped
                )
                payload = (
                    self._normalize_hex_payload(data)
                    if looks_like_hex
                    else data.encode("utf-8")
                )
            elif as_text:
                payload = data.encode("utf-8")
            else:
                payload = self._normalize_hex_payload(data)
        else:
            raise TypeError("send_raw() 需要 bytes 或 str")
        return self._enqueue_tx(payload, "TX", metadata)

    def _invoke_tx_callback(self, request: TxRequest, ts: float) -> None:
        callback = self.on_tx_sent
        if callback is None:
            return
        try:
            try:
                parameter_count = len(
                    inspect.signature(callback).parameters
                )
            except (TypeError, ValueError):
                parameter_count = 4
            if parameter_count >= 4:
                callback(
                    request.payload,
                    request.direction_label,
                    ts,
                    dict(request.metadata),
                )
            else:
                callback(request.payload, request.direction_label, ts)
        except Exception as exc:
            self._notify_error(f"TX 回调异常: {exc}")

    def _tx_loop(self, gen: int) -> None:
        tx_queue = self._tx_queue
        serial_obj = self._serial
        if tx_queue is None or serial_obj is None:
            return
        try:
            while True:
                if gen != self._generation:
                    # Stale generation — drain remaining items before
                    # returning so external queue.join() does not hang.
                    self._drain_tx_queue(tx_queue)
                    break
                try:
                    item = tx_queue.get(timeout=0.1)
                except queue.Empty:
                    if self._stop_event.is_set():
                        break
                    continue
                try:
                    if item is _TX_STOP:
                        break
                    request = item
                    if not isinstance(request, TxRequest):
                        continue
                    if self._stop_event.is_set() and not getattr(
                        serial_obj, "is_open", False
                    ):
                        break
                    try:
                        write_lock = self._write_lock
                        if write_lock is None:
                            raise SerialStateError("串口发送锁未初始化")
                        with write_lock:
                            cur_serial = self._serial
                            if (
                                cur_serial is not serial_obj
                                or not getattr(serial_obj, "is_open", False)
                            ):
                                raise SerialStateError(
                                    "串口连接已失效，请重新开始监控"
                                )
                            written = serial_obj.write(request.payload)
                            if written != len(request.payload):
                                raise SerialOperationError(
                                    f"串口只写入 {written}/{len(request.payload)} 字节"
                                )
                    except SerialTimeoutException as exc:
                        self._notify_error(
                            f"串口写超时，本次发送已丢弃: {exc}"
                        )
                        continue
                    except (
                        serial.SerialException,
                        OSError,
                        SerialOperationError,
                    ) as exc:
                        intentional_stop = self._stop_event.is_set()
                        with self._state_lock:
                            if gen == self._generation:
                                self.running = False
                                self._connected = False
                                self._stop_event.set()
                        if not intentional_stop:
                            kind = _classify_serial_error(exc)
                            self._notify_connection_error(
                                f"串口写入错误: "
                                f"{_friendly_serial_error(self.port, exc, kind)}",
                                kind,
                            )
                        break
                    self._invoke_tx_callback(request, time.time())
                finally:
                    try:
                        tx_queue.task_done()
                    except ValueError:
                        pass
        finally:
            # Balanced drain on any exit path (generation mismatch,
            # stop signal, exception, or normal termination).
            # The ``finally`` block does NOT run when the loop is
            # still active; only when ``break``/exception has been
            # unwound. When the loop breaks because ``gen`` became
            # stale, the early drain above already handled the
            # remainder, so this block is a safety net for the
            # ``_TX_STOP`` and error branches that ``task_done``
            # their own item via the inner ``finally``.
            # We avoid double-draining by checking whether the queue
            # still has unfinished work that was NOT balanced by the
            # inner ``task_done`` call: any leftover items represent
            # requests that were never pulled by this worker (e.g.
            # arrived after the loop had already exited via
            # ``_TX_STOP``). Draining them here prevents leaks.
            if tx_queue is not None:
                self._drain_tx_queue(tx_queue)

    @staticmethod
    def _drain_tx_queue(q: "queue.Queue") -> None:
        """Best-effort drain: empty the queue and call ``task_done`` for each.

        Caller should only invoke this once per queue lifecycle; otherwise
        ``task_done`` may raise ``ValueError`` (handled below).
        """
        while True:
            try:
                q.get_nowait()
            except queue.Empty:
                break
            try:
                q.task_done()
            except ValueError:
                pass

    def _read_loop_optimized(self, gen: int) -> None:
        """RX thread — reads raw bytes and pushes them into the parse queue.

        Keeps the serial RX path strictly focused on consuming bytes as
        fast as possible. All frame sync, plugin execution, and callback
        dispatch happens in the separate ``_parse_loop`` thread to prevent
        slow GUI/log delays from blocking the next read.
        """
        serial_obj = self._serial
        parse_queue = self._parse_queue
        if serial_obj is None or parse_queue is None:
            return
        try:
            while not self._stop_event.is_set():
                if gen != self._generation:
                    break
                try:
                    raw = serial_obj.read(4096)
                except (serial.SerialException, OSError) as exc:
                    if self._stop_event.is_set() or gen != self._generation:
                        break
                    with self._state_lock:
                        if gen != self._generation:
                            break
                        self.running = False
                        self._connected = False
                        self._stop_event.set()
                    kind = _classify_serial_error(exc)
                    self._error_count += 1
                    self._notify_connection_error(
                        f"串口读取错误: "
                        f"{_friendly_serial_error(self.port, exc, kind)}",
                        kind,
                    )
                    self._schedule_reconnect(gen)
                    break
                if not raw:
                    continue
                try:
                    parse_queue.put_nowait(bytes(raw))
                except queue.Full:
                    # Back-pressure: the parser cannot keep up. Drop the
                    # oldest chunk to make room and record the overflow.
                    try:
                        parse_queue.get_nowait()
                    except queue.Empty:
                        pass
                    try:
                        parse_queue.put_nowait(bytes(raw))
                    except queue.Full:
                        pass
                    self.rx_parse_overflows += 1
        except Exception as exc:
            if not self._stop_event.is_set():
                with self._state_lock:
                    if gen == self._generation:
                        self.running = False
                        self._connected = False
                        self._stop_event.set()
                kind = _classify_serial_error(exc)
                self._error_count += 1
                self._notify_connection_error(f"采集异常: {exc}", kind)
        finally:
            # Only close the specific serial handle this thread used.
            # Do NOT touch self._serial — it may have been replaced
            # by a reconnect thread.
            self._close_serial_safe(serial_obj)

    def _parse_loop(self, gen: int) -> None:
        """Parser/dispatch thread — frame sync, plugins, callbacks.

        Consumes raw byte chunks from ``_parse_queue`` and performs
        frame synchronization, plugin execution, and callback dispatch
        OUTSIDE the RX thread so slow callbacks cannot starve reads.
        """
        parse_queue = self._parse_queue
        if parse_queue is None:
            return
        sync = self.sync
        raw_buf = bytearray()
        last_flush = time.time()

        def flush_raw(force: bool = False) -> None:
            nonlocal raw_buf, last_flush
            if not raw_buf:
                return
            now = time.time()
            age_ms = (now - last_flush) * 1000.0
            if (
                not force
                and len(raw_buf) < self.raw_batch_bytes
                and age_ms < self.raw_batch_ms
            ):
                return
            data = bytes(raw_buf)
            raw_buf.clear()
            last_flush = now
            if self.on_raw:
                try:
                    self.on_raw(data, now)
                except Exception as exc:
                    if self.on_error:
                        self.on_error(
                            f"原始数据回调异常（已跳过）: {exc}"
                        )

        try:
            while not self._stop_event.is_set():
                if gen != self._generation:
                    break
                try:
                    raw = parse_queue.get(timeout=0.1)
                except queue.Empty:
                    flush_raw(False)
                    continue
                if self._stop_event.is_set():
                    break
                if gen != self._generation:
                    break
                now = time.time()

                if self.on_mcu_frame is not None:
                    self._dispatch_mcu_frames(raw, now)
                if not self.primary_enabled:
                    continue

                if self.raw_mode:
                    raw_buf.extend(raw)
                    flush_raw(False)
                    continue
                frame_cfg = (
                    self.cfg.get("frame", {}) if self.cfg else {}
                )
                if not frame_cfg:
                    raw_buf.extend(raw)
                    flush_raw(False)
                    continue

                flush_raw(True)
                try:
                    frames = sync.feed(raw)
                except Exception as exc:
                    self._error_count += 1
                    self._notify_error(
                        f"主协议组帧异常（已跳过本批数据）: {exc}"
                    )
                    continue
                noise = bytes(sync.last_noise or b"")
                if noise and self.on_raw:
                    try:
                        self.on_raw(noise, now)
                    except Exception as exc:
                        self._notify_error(
                            f"非协议数据回调异常（已跳过）: {exc}"
                        )
                for frame in frames:
                    try:
                        result = parse_frame(
                            frame.raw, self.cfg, direction=self.direction
                        )
                    except Exception as exc:
                        self._error_count += 1
                        self._notify_error(
                            f"帧解析异常（已跳过）: {exc}"
                        )
                        continue
                    if self.on_frame:
                        try:
                            self.on_frame(result, frame, now)
                        except Exception as exc:
                            self._notify_error(
                                f"回调异常（已跳过）: {exc}"
                            )
            flush_raw(True)
        except Exception as exc:
            if not self._stop_event.is_set():
                with self._state_lock:
                    if gen == self._generation:
                        self.running = False
                        self._connected = False
                        self._stop_event.set()
                self._notify_connection_error(
                    f"解析异常: {exc}",
                )

    def _schedule_reconnect(self, old_gen: int) -> None:
        """Launch a background reconnect supervisor thread.

        The RX thread must NEVER run reconnect directly — that would
        cause self-join deadlock and resource leaks.
        """
        with self._state_lock:
            if self._stopping:
                return
            if self._reconnect_thread is not None and self._reconnect_thread.is_alive():
                return
            self._reconnect_thread = threading.Thread(
                target=self._reconnect_supervisor,
                args=(old_gen,),
                daemon=True,
                name=f"smst-reconnect-{self.port}",
            )
            self._reconnect_thread.start()

    def _reconnect_supervisor(self, failed_gen: int) -> None:
        """Background reconnect loop — runs in its own daemon thread.

        Acquires serial under lock, releases, closes outside lock,
        then opens a new serial under lock. Never runs inside the
        RX thread's stack.
        """
        attempt = 0
        while not self._stop_event.is_set() and attempt < self.max_reconnect_attempts:
            attempt += 1
            self._notify_error(
                f"尝试重连 {attempt}/{self.max_reconnect_attempts}..."
            )
            if self._stop_event.is_set():
                break
            time.sleep(self.reconnect_delay)
            if self._stop_event.is_set():
                break
            try:
                # --- Phase 1: close old serial under lock ---
                with self._state_lock:
                    if self._stopping:
                        return
                    cur_gen = self._generation
                    if cur_gen != failed_gen:
                        # Another reconnect already succeeded
                        return
                    old_serial = self._serial
                    self._serial = None
                    self._connected = False

                # --- Phase 2: close outside lock ---
                self._close_serial_safe(old_serial)

                # --- Phase 3: open new serial ---
                new_serial = self._open_serial()

                # --- Phase 4: install under lock ---
                with self._state_lock:
                    if self._stopping:
                        self._close_serial_safe(new_serial)
                        return
                    self._serial = new_serial
                    self._connected = True
                    self._generation += 1
                    new_gen = self._generation

                self._notify_error("串口重连成功")

                # --- Phase 5: wait for old workers, then spawn replacements ---
                with self._state_lock:
                    if self._stopping:
                        self._close_serial_safe(new_serial)
                        return
                    old_workers = (
                        self._thread,
                        self._parse_thread,
                        self._tx_thread,
                    )
                    self._stop_event.clear()
                    self.running = True
                    self._parse_queue = queue.Queue(
                maxsize=max(1, int(self.parse_queue_size))
            )

                for worker in old_workers:
                    if worker is not None and worker.is_alive():
                        worker.join(timeout=0.5)

                with self._state_lock:
                    if self._stopping:
                        self._close_serial_safe(new_serial)
                        return
                    self._parse_thread = threading.Thread(
                        target=self._parse_loop,
                        args=(new_gen,),
                        daemon=True,
                        name=f"smst-parse-{self.port}",
                    )
                    self._thread = threading.Thread(
                        target=self._read_loop_optimized,
                        args=(new_gen,),
                        daemon=True,
                        name=f"smst-rx-{self.port}",
                    )
                    self._parse_thread.start()
                    self._thread.start()
                    if self._tx_thread is None or not self._tx_thread.is_alive():
                        self._tx_thread = threading.Thread(
                            target=self._tx_loop,
                            args=(new_gen,),
                            daemon=True,
                            name=f"smst-tx-{self.port}",
                        )
                        self._tx_thread.start()
                return
            except (serial.SerialException, OSError) as exc:
                kind = _classify_serial_error(exc)
                self._error_count += 1
                self._notify_connection_error(
                    f"重连失败: "
                    f"{_friendly_serial_error(self.port, exc, kind)}",
                    kind,
                )
        with self._state_lock:
            self._reconnect_thread = None

    @staticmethod
    def list_ports() -> list[dict]:
        if not HAS_SERIAL:
            return []
        return [
            {
                "device": p.device,
                "description": p.description,
                "hwid": p.hwid,
            }
            for p in serial.tools.list_ports.comports()
        ]


@dataclass
class TxRequest:
    payload: bytes
    direction_label: str = "TX"
    metadata: dict[str, Any] = field(default_factory=dict)


_TX_STOP = object()