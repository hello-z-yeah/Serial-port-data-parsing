"""Optimized serial data collector with memory management and thread safety."""

from __future__ import annotations
import queue
import threading
import time
import select
from typing import Callable, Any
from contextlib import contextmanager

from .exceptions import (
    ProtocolParserError,
    SerialOperationError,
    SerialStateError,
    TxQueueFullError,
    log_protocol_error,
    classify_protocol_error
)
from .parser import parse_frame

try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False


class OptimizedSerialCollector:
    """优化版本的串口数据收集器，具有内存管理和线程安全特性"""
    
    def __init__(
        self,
        cfg: dict,
        port: str,
        baudrate: int = 9600,
        timeout: float = 1.0,
        parity: str = 'N',
        stopbits: int = 1,
        bytesize: int = 8,
        *,
        on_frame: Callable | None = None,
        on_raw: Callable | None = None,
        on_error: Callable | None = None,
        on_mcu_frame: Callable | None = None,
        mcu_cfg: dict | None = None,
        primary_enabled: bool = True,
        raw_mode: bool = False,
        raw_batch_bytes: int = 4096,
        raw_batch_ms: int = 100,
        tx_queue_size: int = 100,
        max_buffer_size: int = 1024 * 1024,  # 新增：最大缓冲区大小限制
        max_reconnect_attempts: int = 5,      # 新增：最大重连次数
        reconnect_delay: float = 2.0,        # 新增：重连延迟
    ):
        """初始化优化版本的串口收集器
        
        Args:
            cfg: 协议配置
            port: 串口设备名
            baudrate: 波特率
            on_frame: 帧解析回调
            on_raw: 原始数据回调
            on_error: 错误回调
            on_mcu_frame: MCU帧回调
            mcu_cfg: MCU配置
            primary_enabled: 是否启用主协议解析
            raw_mode: 是否启用原始数据模式
            raw_batch_bytes: 原始数据批量处理大小
            raw_batch_ms: 原始数据批量处理超时
            tx_queue_size: 发送队列大小
            max_buffer_size: 最大缓冲区大小（字节）
            max_reconnect_attempts: 最大重连次数
            reconnect_delay: 重连延迟（秒）
        """
        if not HAS_SERIAL:
            raise SerialStateError("pyserial 未安装，请执行: pip install pyserial")
        
        self.cfg = cfg
        self.port = port
        self.baudrate = baudrate
        self.on_frame = on_frame
        self.on_raw = on_raw
        self.on_error = on_error
        self.on_mcu_frame = on_mcu_frame
        self.mcu_cfg = mcu_cfg or {}
        self.primary_enabled = primary_enabled
        self.raw_mode = raw_mode
        self.raw_batch_bytes = raw_batch_bytes
        self.raw_batch_ms = raw_batch_ms
        self.tx_queue_size = tx_queue_size
        self.max_buffer_size = max_buffer_size
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_delay = reconnect_delay
        
        # 使用可重入锁改进线程安全
        self._state_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._running = False
        self._stopping = False
        self._connection_failures = 0
        self._last_error_time = 0
        
        # 性能监控
        self._performance_monitor = PerformanceMonitor()
        
        # 健康检查
        self._health_checker = HealthChecker(self)
        
        # 资源管理
        self._resources = set()
        self._cleanup_callbacks = []
        
        # 线程
        self._serial: serial.Serial | None = None
        self._sync: FrameSynchronizer | None = None
        self._mcu_sync: FrameSynchronizer | None = None
        self._thread: threading.Thread | None = None
        self._tx_thread: threading.Thread | None = None
        self._tx_queue: queue.Queue | None = None
        self._mcu_sync_lock = threading.Lock()
        self.tx_dropped_on_stop = 0

    def _register_resource(self, resource) -> None:
        """注册需要管理的资源"""
        self._resources.add(resource)
    
    def _register_cleanup(self, callback) -> None:
        """注册清理回调"""
        self._cleanup_callbacks.append(callback)
    
    def _cleanup_resource(self, resource) -> None:
        """清理单个资源"""
        try:
            if hasattr(resource, 'close'):
                resource.close()
            elif hasattr(resource, 'stop'):
                resource.stop()
        except Exception as exc:
            if self.on_error:
                self.on_error(f"资源清理失败: {exc}")
            log_protocol_error(exc, "资源清理失败")
    
    def cleanup_all(self) -> None:
        """清理所有资源"""
        # 执行清理回调
        for callback in self._cleanup_callbacks:
            try:
                callback()
            except Exception as exc:
                if self.on_error:
                    self.on_error(f"清理回调失败: {exc}")
                log_protocol_error(exc, "清理回调失败")
        
        # 清理资源
        for resource in list(self._resources):
            self._cleanup_resource(resource)
        
        self._resources.clear()
        self._cleanup_callbacks.clear()

    @contextmanager
    def _state_context(self):
        """状态管理的上下文管理器，避免死锁"""
        with self._state_lock:
            yield self
    
    def _check_serial_connection(self, serial_obj) -> bool:
        """检查串口连接状态"""
        try:
            # 尝试读取少量数据以检查连接
            serial_obj.in_waiting
            return True
        except (serial.SerialException, OSError):
            return False

    def _handle_connection_loss(self) -> None:
        """处理连接丢失"""
        if self._connection_failures >= self._max_reconnect_attempts:
            self._notify_connection_error("连接失败次数过多，停止重试", "max_retries_exceeded")
            self.running = False
            return
        
        self._connection_failures += 1
        self._notify_error(f"连接丢失，尝试重连 ({self._connection_failures}/{self._max_reconnect_attempts})")
        
        # 执行重连
        self._reconnect()

    def _reconnect(self) -> None:
        """重新连接串口"""
        try:
            self.stop()
            time.sleep(self.reconnect_delay)
            self.start()
        except Exception as exc:
            self._notify_error(f"重连失败: {exc}")
            log_protocol_error(exc, "串口重连失败")

    def start(self) -> None:
        """启动串口收集器"""
        with self._state_context():
            if self._running:
                return
            if self._stopping:
                raise SerialStateError("串口正在停止中，请稍候再启动")
            if self._thread is not None and self._thread.is_alive():
                raise SerialStateError("上一条串口读取线程仍在停止中，请稍后重试")
            if self._tx_thread is not None and self._tx_thread.is_alive():
                raise SerialStateError("上一条串口发送线程仍在停止中，请稍后重试")

            self._sync = FrameSynchronizer(self.cfg or {}, max_buffer_size=self.max_buffer_size)
            self._register_resource(self._sync)
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
                self._register_resource(self._serial)
            except (serial.SerialException, OSError) as exc:
                self._serial = None
                kind = _classify_serial_error(exc)
                error = SerialOperationError(_friendly_serial_error(self.port, exc, kind))
                setattr(error, "kind", kind)
                raise error from exc

            try:
                self._write_lock = self._write_lock or threading.Lock()
                self._tx_queue = queue.Queue(maxsize=max(1, int(self.tx_queue_size)))
                self._register_resource(self._tx_queue)
                self._stop_event.clear()
                self.tx_dropped_on_stop = 0
                self._connection_failures = 0
                self._running = True
                self._tx_thread = threading.Thread(
                    target=self._tx_loop,
                    daemon=True,
                    name=f"smst-tx-{self.port}",
                )
                self._thread = threading.Thread(
                    target=self._read_loop_optimized,
                    daemon=True,
                    name=f"smst-rx-{self.port}",
                )
                self._tx_thread.start()
                self._thread.start()
            except (RuntimeError, TypeError, ValueError, OverflowError) as exc:
                self._running = False
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
                raise SerialOperationError(f"无法启动串口工作线程: {exc}") from exc

    def _read_loop_optimized(self) -> None:
        """优化的读取循环，包含非阻塞I/O和批量处理"""
        serial_obj = self._serial
        sync = self._sync
        if serial_obj is None or sync is None:
            return
        
        raw_buf = bytearray()
        last_flush = time.time()
        batch_size = self.raw_batch_bytes
        batch_timeout = self.raw_batch_ms / 1000.0
        
        def flush_raw(force: bool = False) -> None:
            nonlocal raw_buf, last_flush
            if not raw_buf:
                return
            now = time.time()
            age_ms = (now - last_flush) * 1000.0
            if not force and len(raw_buf) < batch_size and age_ms < batch_timeout:
                return
            
            # 检查缓冲区大小限制
            if len(raw_buf) > self.max_buffer_size:
                # 丢弃旧数据，保留最新部分
                discard_size = len(raw_buf) - self.max_buffer_size
                raw_buf = raw_buf[discard_size:]
                sync.error_count += discard_size
                sync.last_discarded += discard_size
            
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
            while self._running and not self._stop_event.is_set():
                try:
                    # 使用非阻塞读取，避免长时间阻塞
                    ready, _, _ = select.select([serial_obj], [], [], 0.1)
                    if ready:
                        raw = serial_obj.read(4096)
                    else:
                        raw = b""
                except (serial.SerialException, OSError) as exc:
                    if self._stop_event.is_set() or not self._running:
                        break
                    self._running = False
                    self._stop_event.set()
                    kind = _classify_serial_error(exc)
                    self._notify_connection_error(
                        f"串口读取错误: {_friendly_serial_error(self.port, exc, kind)}",
                        kind,
                    )
                    break
                
                # 检查连接状态
                if not self._check_serial_connection(serial_obj):
                    self._handle_connection_loss()
                    continue
                
                if not raw:
                    flush_raw(False)
                    continue
                
                now = time.time()
                
                # 处理MCU帧
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
                
                # 噪声处理
                noise = bytes(sync.last_noise or b"")
                if noise and self.on_raw:
                    try:
                        self.on_raw(noise, now)
                    except Exception as exc:
                        if self.on_error:
                            self.on_error(f"非协议数据回调异常（已跳过）: {exc}")
                        log_protocol_error(exc, "非协议数据回调异常")
                
                # 处理帧
                ok_count = 0
                for frame in frames:
                    try:
                        result = parse_frame(frame.raw, self.cfg, direction=self.direction)
                        ok_count += 1
                    except ProtocolParserError as exc:
                        log_protocol_error(exc, "帧解析异常")
                        self._notify_error(f"帧解析异常（已跳过）: {exc}")
                        continue
                    
                    if self.on_frame:
                        try:
                            self.on_frame(result, frame, now)
                        except ProtocolParserError as exc:
                            log_protocol_error(exc, "帧回调异常")
                            self._notify_error(f"回调异常（已跳过）: {exc}")
                
                # 更新性能监控
                if ok_count > 0:
                    self._performance_monitor.record_frame(time.time() - now)
            
            flush_raw(True)
        except ProtocolParserError as exc:
            if not self._stop_event.is_set():
                self._running = False
                self._stop_event.set()
                log_protocol_error(exc, "串口采集异常")
                self._notify_connection_error(f"采集异常: {exc}", getattr(exc, 'kind', 'unknown'))
        except Exception as exc:
            if not self._stop_event.is_set():
                self._running = False
                self._stop_event.set()
                friendly, _ = classify_protocol_error(exc)
                log_path = log_protocol_error(exc, "串口采集未知错误")
                self._notify_connection_error(f"采集异常: {friendly}", 'unknown')
        finally:
            self._running = False

    def _notify_error(self, msg: str) -> None:
        """通知错误"""
        if self.on_error:
            try:
                self.on_error(msg)
            except Exception as exc:
                log_protocol_error(exc, "错误回调异常")

    def _notify_connection_error(self, msg: str, kind: str) -> None:
        """通知连接错误"""
        self._notify_error(msg)
        self._last_error_time = time.time()

    def request_stop(self) -> None:
        """请求停止工作线程"""
        with self._state_context():
            self._stopping = True
            self._running = False
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
        """停止工作线程"""
        self.request_stop()
        with self._state_context():
            serial_obj = self._serial
            rx_thread = self._thread
            tx_thread = self._tx_thread
            tx_queue = self._tx_queue
        deadline = time.monotonic() + max(0.0, float(timeout))

        # 关闭串口
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
            with self._state_context():
                self._stopping = False
            raise SerialOperationError(f"串口线程未能及时停止：{', '.join(alive)}")

        with self._state_context():
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
            if self._sync is not None:
                self._sync.reset()
            with self._mcu_sync_lock:
                if self._mcu_sync is not None:
                    self._mcu_sync.reset()
            self._stopping = False
            self._running = False
        if dropped:
            self._notify_error(f"串口停止时丢弃了 {dropped} 条尚未发送的 TX 请求")

    def stop_async(
        self,
        *,
        timeout: float = 2.5,
    ) -> threading.Thread:
        """异步停止工作线程"""
        stop_thread = threading.Thread(
            target=self.stop,
            kwargs={"timeout": timeout},
            daemon=True,
            name=f"smst-stop-{self.port}",
        )
        stop_thread.start()
        return stop_thread

    def _dispatch_mcu_frames(self, data: bytes, ts: float) -> None:
        """分发MCU帧"""
        if self._mcu_sync is None:
            with self._mcu_sync_lock:
                if self._mcu_sync is None:
                    self._mcu_sync = FrameSynchronizer(self.mcu_cfg)
                    self._register_resource(self._mcu_sync)
        
        try:
            frames = self._mcu_sync.feed(data)
        except Exception as exc:
            self._notify_error(f"MCU组帧异常: {exc}")
            return
        
        for frame in frames:
            try:
                result = parse_frame(frame.raw, self.mcu_cfg)
            except Exception as exc:
                self._notify_error(f"MCU帧解析异常: {exc}")
                continue
            if self.on_mcu_frame:
                try:
                    self.on_mcu_frame(result, frame, ts)
                except Exception as exc:
                    self._notify_error(f"MCU帧回调异常: {exc}")

    def set_mcu_cfg(self, cfg: dict) -> None:
        """设置MCU配置"""
        self.mcu_cfg = cfg
        with self._mcu_sync_lock:
            if self._mcu_sync is not None:
                self._mcu_sync.reset()
                self._mcu_sync = FrameSynchronizer(cfg)
                self._register_resource(self._mcu_sync)

    @property
    def running(self) -> bool:
        """获取运行状态"""
        return self._running

    @running.setter
    def running(self, value: bool) -> None:
        """设置运行状态"""
        with self._state_context():
            self._running = value

    @property
    def direction(self) -> str:
        """获取数据方向"""
        return getattr(self, "_direction", "request")

    @direction.setter
    def direction(self, value: str) -> None:
        """设置数据方向"""
        self._direction = value

    @property
    def stopbits(self) -> float:
        """获取停止位"""
        return getattr(self, "_stopbits", 1.0)

    @stopbits.setter
    def stopbits(self, value: float) -> None:
        """设置停止位"""
        self._stopbits = value

    @property
    def bytesize(self) -> int:
        """获取数据位"""
        return getattr(self, "_bytesize", 8)

    @bytesize.setter
    def bytesize(self, value: int) -> None:
        """设置数据位"""
        self._bytesize = value

    def get_health_status(self) -> dict:
        """获取健康状态"""
        return self._health_checker.check_health()

    def get_performance_metrics(self) -> dict:
        """获取性能指标"""
        metrics = self._performance_monitor.get_metrics()
        return {
            "frame_count": metrics.frame_count,
            "error_count": metrics.error_count,
            "avg_processing_time": metrics.avg_processing_time,
            "memory_usage": metrics.memory_usage,
            "cpu_usage": metrics.cpu_usage,
            "connection_failures": self._connection_failures,
            "buffer_size": len(self._sync.buffer) if self._sync else 0,
        }

    @staticmethod
    def list_ports() -> list[dict]:
        """列出可用串口"""
        if not HAS_SERIAL:
            return []
        return [
            {"device": p.device, "description": p.description, "hwid": p.hwid}
            for p in serial.tools.list_ports.comports()
        ]


# 性能监控类
class PerformanceMonitor:
    """性能监控类"""
    
    def __init__(self, window_size: int = 100):
        self.window_size = window_size
        self.processing_times = []
        self.start_time = time.time()
    
    def record_frame(self, processing_time: float) -> None:
        """记录帧处理时间"""
        self.processing_times.append(processing_time)
        if len(self.processing_times) > self.window_size:
            self.processing_times.pop(0)
    
    def get_metrics(self) -> dict:
        """获取性能指标"""
        avg_time = sum(self.processing_times) / len(self.processing_times) if self.processing_times else 0
        return {
            "frame_count": len(self.processing_times),
            "error_count": 0,  # 需要从外部传入
            "avg_processing_time": avg_time,
            "memory_usage": self._get_memory_usage(),
            "cpu_usage": self._get_cpu_usage(),
        }
    
    def _get_memory_usage(self) -> float:
        """获取内存使用情况"""
        try:
            import psutil
            process = psutil.Process()
            return process.memory_info().rss / 1024 / 1024  # MB
        except Exception:
            return 0.0
    
    def _get_cpu_usage(self) -> float:
        """获取CPU使用情况"""
        try:
            import psutil
            return psutil.cpu_percent()
        except Exception:
            return 0.0


# 健康检查类
class HealthChecker:
    """健康检查类"""
    
    def __init__(self, collector: OptimizedSerialCollector):
        self.collector = collector
        self.last_check_time = time.time()
        self.health_status = "healthy"
        self.issues = []
    
    def check_health(self) -> dict:
        """执行健康检查"""
        issues = []
        
        # 检查串口连接
        if not self._check_serial_connection():
            issues.append("串口连接异常")
        
        # 检查内存使用
        if self._check_memory_usage():
            issues.append("内存使用过高")
        
        # 检查线程状态
        if not self._check_thread_status():
            issues.append("线程状态异常")
        
        # 检查错误率
        if self._check_error_rate():
            issues.append("错误率过高")
        
        # 检查缓冲区大小
        if self._check_buffer_size():
            issues.append("缓冲区大小过大")
        
        self.issues = issues
        self.health_status = "healthy" if not issues else f"unhealthy: {', '.join(issues)}"
        self.last_check_time = time.time()
        
        return {
            "status": self.health_status,
            "issues": issues,
            "last_check": self.last_check_time,
        }
    
    def _check_serial_connection(self) -> bool:
        """检查串口连接"""
        try:
            if self.collector._serial is None:
                return False
            
            # 检查串口是否打开
            if not getattr(self.collector._serial, 'is_open', False):
                return False
            
            # 检查是否有数据等待处理
            return self.collector._serial.in_waiting >= 0
        except Exception:
            return False
    
    def _check_memory_usage(self) -> bool:
        """检查内存使用"""
        try:
            import psutil
            process = psutil.Process()
            memory_mb = process.memory_info().rss / 1024 / 1024
            return memory_mb > 500  # 超过500MB认为过高
        except Exception:
            return False
    
    def _check_thread_status(self) -> bool:
        """检查线程状态"""
        try:
            return (
                self.collector._thread is not None and 
                self.collector._thread.is_alive() and
                self.collector._tx_thread is not None and 
                self.collector._tx_thread.is_alive()
            )
        except Exception:
            return False
    
    def _check_error_rate(self) -> bool:
        """检查错误率"""
        try:
            if not hasattr(self.collector, '_sync'):
                return False
            
            sync = self.collector._sync
            if sync is None:
                return False
            
            total_frames = getattr(sync, 'frame_count', 0)
            total_errors = getattr(sync, 'error_count', 0)
            
            if total_frames == 0:
                return False
            
            error_rate = total_errors / total_frames
            return error_rate > 0.1  # 错误率超过10%认为过高
        except Exception:
            return False
    
    def _check_buffer_size(self) -> bool:
        """检查缓冲区大小"""
        try:
            if not hasattr(self.collector, '_sync'):
                return False
            
            sync = self.collector._sync
            if sync is None:
                return False
            
            buffer_size = len(getattr(sync, 'buffer', []))
            max_size = getattr(self.collector, 'max_buffer_size', 1024 * 1024)
            
            return buffer_size > max_size * 0.8  # 超过80%认为过大
        except Exception:
            return False


# 保持向后兼容的别名
SerialCollector = OptimizedSerialCollector