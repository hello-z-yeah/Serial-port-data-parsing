"""串口帧同步与数据采集模块。

从连续的字节流中识别并切出完整的 V3.0 协议帧，
供解析器使用。
"""
from __future__ import annotations

import inspect
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .exceptions import (
    ProtocolParserError,
    SerialOperationError,
    SerialStateError,
    TxQueueFullError,
    log_protocol_error,
    classify_protocol_error
)
from .parser import (
    Frame,
    ParseResult,
    ProtocolError,
    load_protocol,
    parse_frame,
    split_frame,
    to_hex,
)

try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
    SerialTimeoutException = serial.SerialTimeoutException
except ImportError:
    HAS_SERIAL = False

    class SerialTimeoutException(Exception):
        pass


def _classify_serial_error(exc: BaseException) -> str:
    """Classify pyserial/OS failures without depending on localized wording."""
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, PermissionError):
            return "busy"
        if isinstance(current, FileNotFoundError):
            return "not_found"
        winerror = getattr(current, "winerror", None)
        errno = getattr(current, "errno", None)
        if winerror in (5, 32) or errno in (13,):
            return "busy"
        if winerror in (2, 3, 1167) or errno in (2, 19):
            return "not_found"
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    msg = str(exc).lower()
    if any(token in msg for token in ("access is denied", "permission denied", "拒绝访问", "被占用", "resource busy")):
        return "busy"
    if any(token in msg for token in ("file not found", "not found", "cannot find", "找不到", "不存在", "disconnected", "device not configured")):
        return "not_found"
    if any(token in msg for token in ("unplug", "拔出", "device removed", "i/o error", "input/output error")):
        return "unplugged"
    return "io"


def _friendly_serial_error(port: str, exc: BaseException, kind: str) -> str:
    if kind == "busy":
        return f"串口 {port} 被占用或无访问权限，请关闭其他串口程序后重试"
    if kind in ("not_found", "unplugged"):
        return f"串口 {port} 不存在或设备已拔出，请重新连接设备并刷新端口"
    return f"串口 {port} I/O 错误：{exc}"


@dataclass
class FrameSynchronizer:
    """字节流帧同步器：从任意位置输入字节，输出完整帧。

    工作原理：
    1. 累积字节到缓冲区
    2. 找到帧头（header）
    3. 读取长度字段
    4. 等收齐完整帧（header + ver + cmd + length + data + chk）
    5. 切出一帧并返回
    6. 循环直到缓冲区不足一帧
    """

    cfg: dict
    buffer: bytearray = field(default_factory=bytearray)
    frame_count: int = 0
    error_count: int = 0
    partial_bytes: int = 0
    last_discarded: int = 0  # 最近一次 feed 丢弃的噪声字节数
    last_noise: bytes = b""  # 最近一次 feed 可确定为非协议数据的字节
    max_buffer_size: int = 1024 * 1024  # 最大缓冲区大小限制（默认1MB）
    buffer_cleanup_threshold: float = 0.8  # 清理阈值（超过80%时触发清理）
    on_buffer_overflow: Callable | None = None  # 缓冲区溢出告警回调

    def feed(self, data: bytes) -> list[Frame]:
        """输入一段字节，返回解析出的所有完整帧。"""
        # 检查缓冲区是否即将溢出
        if len(self.buffer) + len(data) > self.max_buffer_size * self.buffer_cleanup_threshold:
            self._handle_buffer_overflow(len(data))
        
        self.buffer.extend(data)
        self.last_discarded = 0
        self.last_noise = b""
        self.partial_bytes = len(self.buffer)
        frames: list[Frame] = []

        while True:
            frame = self._try_extract_one()
            if frame is None:
                break
            frames.append(frame)
            self.frame_count += 1

        self.partial_bytes = len(self.buffer)
        
        # 缓冲区过大时主动清理
        if len(self.buffer) > self.max_buffer_size * 0.5:
            self._aggressive_cleanup()
            
        return frames

    def _handle_buffer_overflow(self, incoming_size: int) -> None:
        """处理缓冲区溢出情况"""
        # 计算需要清理的大小
        target_size = int(self.max_buffer_size * 0.3)  # 保留30%空间
        cleanup_size = len(self.buffer) - target_size
        
        if cleanup_size > 0:
            # 清理最旧的数据（但保留可能的帧头）
            frames_found = self._partial_cleanup(cleanup_size)
            
            # 触发溢出告警
            if self.on_buffer_overflow:
                try:
                    self.on_buffer_overflow({
                        'current_size': len(self.buffer),
                        'max_size': self.max_buffer_size,
                        'incoming_size': incoming_size,
                        'cleanup_size': cleanup_size,
                        'frames_found_during_cleanup': frames_found
                    })
                except Exception:
                    pass

    def _partial_cleanup(self, cleanup_size: int) -> int:
        """部分清理缓冲区，尽量保留完整帧"""
        frames_found = 0
        
        # 先尝试从缓冲区中提取所有完整帧
        temp_buffer = bytearray(self.buffer)
        self.buffer.clear()
        
        # 从临时缓冲区中逐字节处理
        for byte in temp_buffer:
            self.buffer.append(byte)
        
        # 重新同步并提取帧
        while len(self.buffer) > cleanup_size:
            frame = self._try_extract_one()
            if frame:
                frames_found += 1
            else:
                # 如果无法提取帧，直接清理最旧的数据
                if len(self.buffer) > cleanup_size:
                    del self.buffer[:cleanup_size]
                break
        
        return frames_found

    def _aggressive_cleanup(self) -> None:
        """主动清理过大的缓冲区"""
        # 保留最新的数据，丢弃最旧的数据
        target_size = int(self.max_buffer_size * 0.3)
        
        # 尝试先提取所有完整帧
        while True:
            frame = self._try_extract_one()
            if frame:
                self.frame_count += 1
            else:
                break
        
        # 如果仍然过大，强制清理
        if len(self.buffer) > target_size:
            # 保留最后一部分数据（可能包含正在接收的帧）
            excess = len(self.buffer) - target_size
            del self.buffer[:excess]
            self.error_count += excess

    def _try_extract_one(self) -> Frame | None:
        """尝试提取一帧。

        使用 ``bytearray.find`` 一次跳过帧头前的噪声，避免逐字节 ``pop(0)``
        产生 O(n²) 数据搬移；高波特率或混入原始日志时性能更稳定。
        """
        buf = self.buffer
        frame_cfg = self.cfg.get("frame", {})
        header_size = int(frame_cfg.get("header_size", 2))
        expected_header = _parse_int(frame_cfg.get("header", "0xA5A5"))
        length_offset = int(frame_cfg.get("length_offset", 4))
        length_size = int(frame_cfg.get("length_size", 2))
        length_byte_order = frame_cfg.get("length_byte_order", "big")
        checksum_size = int(frame_cfg.get("checksum", {}).get("length", 1))
        min_header = length_offset + length_size
        max_frame = int(frame_cfg.get("max_frame_size", 4096))

        try:
            header_bytes = expected_header.to_bytes(header_size, "big")
        except OverflowError:
            self.error_count += len(buf)
            buf.clear()
            return None

        while True:
            if len(buf) < header_size:
                return None

            header_index = buf.find(header_bytes)
            if header_index < 0:
                # 保留最多 header_size-1 个尾字节，它们可能是跨读取块的半个帧头。
                discard = max(0, len(buf) - header_size + 1)
                if discard:
                    self.last_noise += bytes(buf[:discard])
                    self.error_count += discard
                    self.last_discarded += discard
                    del buf[:discard]
                return None

            if header_index:
                self.last_noise += bytes(buf[:header_index])
                self.error_count += header_index
                self.last_discarded += header_index
                del buf[:header_index]

            if len(buf) < min_header:
                return None

            data_len = int.from_bytes(
                buf[length_offset:length_offset + length_size],
                byteorder=length_byte_order,
            )
            total_len = length_offset + length_size + data_len + checksum_size
            if total_len > max_frame:
                self.error_count += 1
                # 跳过整个帧头（而非只删 1 字节），避免 0xA5A5A5... 连续帧头
                # 导致的 O(n) 次循环
                del buf[:header_size]
                continue

            if len(buf) < total_len:
                return None

            raw = bytes(buf[:total_len])
            try:
                frame = split_frame(raw, self.cfg)
            except ProtocolError:
                self.error_count += 1
                # 校验失败时不能先吞掉整段候选帧；噪声伪帧头后面可能包含
                # 真正帧头。仅跳过一个帧头长度，再重新同步。
                self.last_noise += bytes(buf[:header_size])
                del buf[:header_size]
                continue
            if frame.checksum_ok is False:
                # split_frame 会把校验结果带回 Frame；同步器必须把校验失败的
                # 候选帧视为噪声而不是有效帧，并继续寻找其后的真实帧头。
                self.error_count += 1
                self.last_noise += bytes(buf[:header_size])
                del buf[:header_size]
                continue
            del buf[:total_len]
            return frame

    def reset(self) -> None:
        """清空缓冲区。"""
        self.buffer.clear()
        self.partial_bytes = 0
        self.last_noise = b""


@dataclass(frozen=True)
class TxRequest:
    payload: bytes
    direction_label: str = "TX"
    metadata: dict[str, Any] = field(default_factory=dict)


_TX_STOP = object()


@dataclass
class SerialCollector:
    """Serial reader with independent RX and TX worker threads.

    ``send`` and ``send_raw`` only validate and enqueue data.  The GUI and RX
    parser therefore never block on a USB driver write/flush operation.
    """

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
    # New callback form: (payload, label, timestamp, metadata).  Legacy
    # three-argument callbacks remain supported.
    on_tx_sent: Callable[..., None] | None = None
    tx_queue_size: int = 1000
    running: bool = False
    _thread: threading.Thread | None = None
    _tx_thread: threading.Thread | None = None
    _serial: "serial.Serial | None" = None
    sync: FrameSynchronizer | None = None
    mcu_sync: FrameSynchronizer | None = None
    _write_lock: threading.Lock | None = None
    _mcu_sync_lock: threading.Lock = field(default_factory=threading.Lock)
    _tx_queue: queue.Queue | None = None
    _stop_event: threading.Event = field(default_factory=threading.Event)
    _state_lock: threading.RLock = field(default_factory=threading.RLock)
    _stopping: bool = False
    tx_dropped_on_stop: int = 0
    last_connection_error_kind: str = ""
    last_connection_error: str = ""

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
                self._serial = serial.Serial(
                    port=self.port,
                    baudrate=self.baudrate,
                    bytesize=bytesize_map.get(self.bytesize, serial.EIGHTBITS),
                    parity=serial.PARITY_NONE,
                    stopbits=sb_val,
                    timeout=0.1,
                    write_timeout=1.0,
                )
            except (serial.SerialException, OSError) as exc:
                self._serial = None
                kind = _classify_serial_error(exc)
                error = SerialOperationError(_friendly_serial_error(self.port, exc, kind))
                setattr(error, "kind", kind)
                raise error from exc

            try:
                self._write_lock = self._write_lock or threading.Lock()
                self._tx_queue = queue.Queue(maxsize=max(1, int(self.tx_queue_size)))
                self._stop_event.clear()
                self.tx_dropped_on_stop = 0
                self.last_connection_error_kind = ""
                self.last_connection_error = ""
                self.running = True
                self._tx_thread = threading.Thread(
                    target=self._tx_loop,
                    daemon=True,
                    name=f"smst-tx-{self.port}",
                )
                self._thread = threading.Thread(
                    target=self._read_loop,
                    daemon=True,
                    name=f"smst-rx-{self.port}",
                )
                self._tx_thread.start()
                self._thread.start()
            except (RuntimeError, TypeError, ValueError, OverflowError) as exc:
                self.running = False
                self._stop_event.set()
                try:
                    if self._serial is not None and getattr(self._serial, "is_open", False):
                        self._serial.close()
                except (OSError, AttributeError):
                    pass
                if self._tx_thread is not None and self._tx_thread.is_alive():
                    self._tx_thread.join(timeout=0.5)
                self._thread = None
                self._tx_thread = None
                self._serial = None
                self._tx_queue = None
                raise SerialOperationError(f"无法启动串口工作线程：{exc}") from exc

    def request_stop(self) -> None:
        """Signal both workers and cancel any pending OS-level read/write."""
        with self._state_lock:
            self._stopping = True
            self.running = False
            self._stop_event.set()
            serial_obj = self._serial
            tx_queue = self._tx_queue
        if serial_obj is not None:
            for method_name in ("cancel_read", "cancel_write"):
                try:
                    method = getattr(serial_obj, method_name, None)
                    if callable(method):
                        method()
                except (OSError, AttributeError):
                    pass
        if tx_queue is not None:
            try:
                tx_queue.put_nowait(_TX_STOP)
            except queue.Full:
                pass

    def stop(self, *, timeout: float = 2.5) -> None:
        """Stop workers.  This may block and should not be called on the GUI thread."""
        self.request_stop()
        with self._state_lock:
            serial_obj = self._serial
            rx_thread = self._thread
            tx_thread = self._tx_thread
            tx_queue = self._tx_queue
        deadline = time.monotonic() + max(0.0, float(timeout))

        # Close after cancellation; some Windows USB drivers unblock only on close.
        if serial_obj is not None:
            try:
                if getattr(serial_obj, "is_open", False):
                    serial_obj.close()
            except (OSError, AttributeError):
                pass

        current = threading.current_thread()
        for worker in (rx_thread, tx_thread):
            if worker is None or worker is current:
                continue
            remaining = max(0.0, deadline - time.monotonic())
            worker.join(timeout=remaining)

        alive = [
            worker.name
            for worker in (rx_thread, tx_thread)
            if worker is not None and worker is not current and worker.is_alive()
        ]
        if alive:
            # Keep references so callers cannot accidentally start a second
            # collector while hidden workers are still alive.
            with self._state_lock:
                self._stopping = False
            raise SerialOperationError(f"串口线程未能及时停止：{', '.join(alive)}")

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
            self.tx_dropped_on_stop += dropped
            self._thread = None
            self._tx_thread = None
            self._serial = None
            self._tx_queue = None
            if self.sync is not None:
                self.sync.reset()
            with self._mcu_sync_lock:
                if self.mcu_sync is not None:
                    self.mcu_sync.reset()
            self._stopping = False
            self.running = False
        if dropped:
            self._notify_error(f"串口停止时丢弃了 {dropped} 条尚未发送的 TX 请求")

    def stop_async(
        self,
        *,
        timeout: float = 2.5,
        on_complete: Callable[[], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
    ) -> threading.Thread:
        """Stop in a helper thread and notify through caller-provided callbacks."""
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

        thread = threading.Thread(target=worker, daemon=True, name=f"smst-stop-{self.port}")
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

    def _notify_connection_error(self, message: str, kind: str | None = None) -> None:
        resolved_kind = kind or _classify_serial_error(SerialOperationError(message))
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
            self.mcu_sync = FrameSynchronizer(resolved) if frame_cfg else None

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
                self._notify_error(f"模拟MCU辅助通道组帧异常（主串口继续运行）: {exc}")
                return
        if frames:
            for frame in frames:
                try:
                    result = parse_frame(frame.raw, cfg, direction=self.mcu_direction)
                except Exception:
                    result = None
                try:
                    callback(result, frame, ts)
                except Exception as exc:
                    self._notify_error(f"模拟MCU协议回调异常（已跳过）: {exc}")
            return

        # No complete frame: show raw bytes on the MCU page without forwarding
        # them into the hidden receive-analysis page.
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
            self._notify_error(f"模拟MCU原始数据回调异常（已跳过）: {exc}")

    @staticmethod
    def _normalize_hex_payload(frame_bytes: bytes | bytearray | str) -> bytes:
        from .parser import EncodeFrameError

        if isinstance(frame_bytes, (bytes, bytearray)):
            return bytes(frame_bytes)
        if not isinstance(frame_bytes, str):
            raise TypeError("send() 需要 bytes 或 HEX 字符串")
        text = frame_bytes.strip()
        clean = text.replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")
        if clean.lower().startswith("0x"):
            clean = clean[2:]
        if len(clean) % 2:
            raise EncodeFrameError(
                f"TX HEX 字符数必须为偶数：{frame_bytes!r}"
            )
        try:
            return bytes.fromhex(clean)
        except ValueError as exc:
            raise EncodeFrameError(f"TX HEX 字符串非法：{frame_bytes!r}，原因：{exc}") from exc

    def _enqueue_tx(self, payload: bytes, label: str, metadata: dict[str, Any] | None) -> int:
        serial_obj = self._serial
        if not self.running or serial_obj is None or not getattr(serial_obj, "is_open", False):
            raise SerialStateError("串口未打开，请先开始监控再发送")
        if not payload:
            return 0
        tx_queue = self._tx_queue
        if tx_queue is None:
            raise SerialStateError("串口发送线程尚未启动")
        request = TxRequest(bytes(payload), str(label), dict(metadata or {}))
        try:
            tx_queue.put_nowait(request)
        except queue.Full as exc:
            raise TxQueueFullError("发送队列已满，请降低发送频率或等待队列处理完成") from exc
        return len(payload)

    def send(
        self,
        frame_bytes: bytes | bytearray | str,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        return self._enqueue_tx(self._normalize_hex_payload(frame_bytes), "TX", metadata)

    def send_raw(
        self,
        data,
        *,
        as_text: bool | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        if isinstance(data, (bytes, bytearray)):
            payload = bytes(data)
        elif isinstance(data, str):
            if as_text is None:
                stripped = data.strip()
                hex_chars = set("0123456789abcdefABCDEF \t\n\r")
                looks_like_hex = bool(stripped) and all(c in hex_chars for c in stripped)
                payload = self._normalize_hex_payload(data) if looks_like_hex else data.encode("utf-8")
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
                parameter_count = len(inspect.signature(callback).parameters)
            except (TypeError, ValueError):
                parameter_count = 4
            if parameter_count >= 4:
                callback(request.payload, request.direction_label, ts, dict(request.metadata))
            else:
                callback(request.payload, request.direction_label, ts)
        except Exception as exc:
            self._notify_error(f"TX 回调异常: {exc}")

    def _tx_loop(self) -> None:
        tx_queue = self._tx_queue
        serial_obj = self._serial
        if tx_queue is None or serial_obj is None:
            return
        while True:
            try:
                item = tx_queue.get(timeout=0.1)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue
            try:
                if item is _TX_STOP:
                    if tx_queue.empty():
                        break
                    continue
                request = item
                if not isinstance(request, TxRequest):
                    continue
                if self._stop_event.is_set() and not getattr(serial_obj, "is_open", False):
                    break
                try:
                    write_lock = self._write_lock
                    if write_lock is None:
                        raise SerialStateError("串口发送锁未初始化")
                    with write_lock:
                        if serial_obj is not self._serial or not getattr(serial_obj, "is_open", False):
                            raise SerialStateError("串口连接已失效，请重新开始监控")
                        written = serial_obj.write(request.payload)
                        if written != len(request.payload):
                            raise SerialOperationError(
                                f"串口只写入 {written}/{len(request.payload)} 字节"
                            )
                except SerialTimeoutException as exc:
                    # 单次写超时只丢弃本请求，不停止 RX，不触发自动重连。
                    self._notify_error(f"串口写超时，本次发送已丢弃: {exc}")
                    continue
                except (serial.SerialException, OSError, SerialOperationError) as exc:
                    intentional_stop = self._stop_event.is_set()
                    self.running = False
                    self._stop_event.set()
                    if not intentional_stop:
                        kind = _classify_serial_error(exc)
                        self._notify_connection_error(
                            f"串口写入错误: {_friendly_serial_error(self.port, exc, kind)}",
                            kind,
                        )
                    break
                self._invoke_tx_callback(request, time.time())
            finally:
                tx_queue.task_done()

    def _read_loop(self) -> None:
        serial_obj = self._serial
        sync = self.sync
        if serial_obj is None or sync is None:
            return
        raw_buf = bytearray()
        last_flush = time.time()

        def flush_raw(force: bool = False) -> None:
            nonlocal raw_buf, last_flush
            if not raw_buf:
                return
            now = time.time()
            age_ms = (now - last_flush) * 1000.0
            if not force and len(raw_buf) < self.raw_batch_bytes and age_ms < self.raw_batch_ms:
                return
            data = bytes(raw_buf)
            raw_buf.clear()
            last_flush = now
            if self.on_raw:
                try:
                    self.on_raw(data, now)
                except Exception as exc:
                    if self.on_error:
                        self.on_error(f"原始数据回调异常（已跳过）: {exc}")
                    log_protocol_error(exc, "串口原始数据回调异常")

        try:
            while self.running and not self._stop_event.is_set():
                try:
                    raw = serial_obj.read(4096)
                except (serial.SerialException, OSError) as exc:
                    if self._stop_event.is_set() or not self.running:
                        break
                    self.running = False
                    self._stop_event.set()
                    kind = _classify_serial_error(exc)
                    self._notify_connection_error(
                        f"串口读取错误: {_friendly_serial_error(self.port, exc, kind)}",
                        kind,
                    )
                    break
                if not raw:
                    flush_raw(False)
                    continue
                now = time.time()

                if self.on_mcu_frame is not None:
                    self._dispatch_mcu_frames(raw, now)
                if not self.primary_enabled:
                    continue

                if self.raw_mode:
                    raw_buf.extend(raw)
                    flush_raw(False)
                    continue
                frame_cfg = self.cfg.get("frame", {}) if self.cfg else {}
                if not frame_cfg:
                    raw_buf.extend(raw)
                    flush_raw(False)
                    continue

                flush_raw(True)
                try:
                    frames = sync.feed(raw)
                except ProtocolParserError as exc:
                    log_protocol_error(exc, "主协议组帧异常")
                    self._notify_error(f"主协议组帧异常（已跳过本批数据）: {exc}")
                    continue
                # 帧模式下只上抛同步器已经确认的噪声。跨读取块的半帧仍留在
                # buffer 中，不能先作为 raw 显示，收齐后又作为协议帧重复显示。
                noise = bytes(sync.last_noise or b"")
                if noise and self.on_raw:
                    try:
                        self.on_raw(noise, now)
                    except Exception as exc:
                        self._notify_error(f"非协议数据回调异常（已跳过）: {exc}")
                ok_count = 0
                for frame in frames:
                    try:
                        result = parse_frame(frame.raw, self.cfg, direction=self.direction)
                    except ProtocolParserError as exc:
                        log_protocol_error(exc, "帧解析异常")
                        self._notify_error(f"帧解析异常（已跳过）: {exc}")
                        continue
                    ok_count += 1
                    if self.on_frame:
                        try:
                            self.on_frame(result, frame, now)
                        except ProtocolParserError as exc:
                            log_protocol_error(exc, "帧回调异常")
                            self._notify_error(f"回调异常（已跳过）: {exc}")
            flush_raw(True)
        except ProtocolParserError as exc:
            if not self._stop_event.is_set():
                self.running = False
                self._stop_event.set()
                log_protocol_error(exc, "串口采集异常")
                self._notify_connection_error(f"采集异常: {exc}", getattr(exc, 'kind', 'unknown'))
        except Exception as exc:
            if not self._stop_event.is_set():
                self.running = False
                self._stop_event.set()
                friendly, _ = classify_protocol_error(exc)
                log_path = log_protocol_error(exc, "串口采集未知错误")
                self._notify_connection_error(f"采集异常: {friendly}", 'unknown')
        finally:
            self.running = False

    @staticmethod
    def list_ports() -> list[dict]:
        if not HAS_SERIAL:
            return []
        return [
            {"device": p.device, "description": p.description, "hwid": p.hwid}
            for p in serial.tools.list_ports.comports()
        ]


def _parse_int(v) -> int:
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        text = v.strip().lower()
        if text.startswith("0x"):
            return int(text, 16)
        return int(text, 0)
    raise ProtocolError(f"无法解析为整数: {v}")
