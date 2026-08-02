"""Qt 线程安全封装：串口收发用 QThread，定时发送用 QTimer。

红线：业务逻辑零修改 —— 这里只是把 protocol_parser.SerialCollector 的回调
通过 Qt 信号转发到 UI 主线程，不重写任何串口/解析/校验逻辑。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, QThread, Signal, Slot, QTimer

from protocol_parser import (
    SerialCollector,
    list_serial_ports,
    ParseResult,
    Frame,
)
from protocol_parser.parser import to_hex


class SerialWorker(QObject):
    """把 SerialCollector 的回调包成 Qt 信号，UI 线程安全接收。

    信号：
      frameReceived(ParseResult, Frame, float) : 解析到一条完整帧
      rawReceived(bytes, float)                 : ASCII / 无协议原始数据
      txSent(bytes, str, float)                 : TX 字节已写入串口
      errorOccurred(str)                        : 串口/解析错误
      portOpened() / portClosed()               : 串口状态切换
    """

    frameReceived = Signal(object, object, float)
    rawReceived = Signal(bytes, float)
    txSent = Signal(bytes, str, float)
    errorOccurred = Signal(str)
    portOpened = Signal()
    portClosed = Signal()

    def __init__(self, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self._collector: Optional[SerialCollector] = None
        self._thread: Optional[QThread] = None
        self._worker_obj: Optional["_CollectorHolder"] = None

    @property
    def is_open(self) -> bool:
        return self._collector is not None and self._collector.running

    def open(
        self,
        cfg: dict,
        port: str,
        baudrate: int,
        bytesize: int = 8,
        stopbits: float = 1.0,
        direction: Optional[str] = None,
        raw_mode: bool = False,
    ) -> None:
        """在工作线程里创建 SerialCollector 并 start。

        使用 QThread + moveToThread 模式：把 holder 对象移到工作线程，
        它的 _start 方法在工作线程里执行 collector.start()（包含阻塞 read_loop）。
        """
        if self.is_open:
            return

        self._thread = QThread()
        self._worker_obj = _CollectorHolder(self)
        self._worker_obj.moveToThread(self._thread)
        self._thread.start()

        # 在工作线程里调用 _start（通过 QTimer.singleShot(0,...) 投递到工作线程事件循环）
        # 也可以直接 invoke，但用 QTimer.singleShot(0, ...) 最稳。
        cfg_copy = dict(cfg or {})
        QTimer.singleShot(0, self._worker_obj, lambda: self._worker_obj._start(
            cfg_copy, port, baudrate, bytesize, stopbits, direction, raw_mode
        ))

    def close(self) -> None:
        """关闭串口并退出工作线程。"""
        if self._worker_obj is not None:
            try:
                # 在工作线程里同步执行 stop
                QTimer.singleShot(0, self._worker_obj, self._worker_obj._stop)
            except Exception:
                pass
        # 给工作线程 1s 时间收尾
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(1500)
            self._thread = None
        self._worker_obj = None
        self._collector = None

    def send(self, payload) -> int:
        """发送数据（线程安全：SerialCollector 内部带 _write_lock）。"""
        if not self.is_open or self._collector is None:
            raise RuntimeError("串口未打开，请先开始监控再发送")
        return self._collector.send(payload)

    def send_raw(self, data, *, as_text: Optional[bool] = None) -> int:
        if not self.is_open or self._collector is None:
            raise RuntimeError("串口未打开，请先开始监控再发送")
        return self._collector.send_raw(data, as_text=as_text)

    def list_ports(self) -> list:
        return list_serial_ports()


class _CollectorHolder(QObject):
    """运行在工作线程里的 SerialCollector 持有者。

    SerialCollector.start() 内部自己开了 threading.Thread 跑 _read_loop，
    所以 holder._start 不会阻塞工作线程的事件循环（read_loop 在子线程里跑）。
    holder 只是用来保证 collector 的创建/销毁发生在工作线程。
    """

    def __init__(self, owner: SerialWorker) -> None:
        super().__init__()
        self._owner = owner

    def _start(self, cfg, port, baudrate, bytesize, stopbits, direction, raw_mode):
        try:
            collector = SerialCollector(
                cfg=cfg,
                port=port,
                baudrate=baudrate,
                bytesize=bytesize,
                stopbits=stopbits,
                direction=direction,
                raw_mode=raw_mode,
                on_frame=self._on_frame,
                on_error=self._on_error,
                on_raw=self._on_raw,
                on_tx_sent=self._on_tx_sent,
            )
            collector.start()
            self._owner._collector = collector
            self._owner.portOpened.emit()
        except Exception as e:
            self._owner.errorOccurred.emit(f"打开串口失败: {e}")

    def _stop(self):
        c = self._owner._collector
        if c is not None:
            try:
                c.stop()
            except Exception:
                pass
            self._owner._collector = None
            self._owner.portClosed.emit()

    # 这些回调在 collector 的内部 threading.Thread 里被调用 —— Qt 信号是线程安全的，
    # 跨线程 emit 会自动把信号投递到接收者所在的线程（UI 主线程）。
    def _on_frame(self, result, frame, ts):
        self._owner.frameReceived.emit(result, frame, float(ts))

    def _on_error(self, msg):
        self._owner.errorOccurred.emit(str(msg))

    def _on_raw(self, data, ts):
        self._owner.rawReceived.emit(bytes(data), float(ts))

    def _on_tx_sent(self, data, dir_label, ts):
        self._owner.txSent.emit(bytes(data), str(dir_label), float(ts))


class CycleSendTimer(QTimer):
    """周期发送定时器：基于 QTimer，指定毫秒间隔循环触发 send 信号。"""

    triggered_send = Signal()

    def __init__(self, interval_ms: int = 1000, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.setInterval(interval_ms)
        self.timeout.connect(self._on_timeout)

    def _on_timeout(self) -> None:
        self.triggered_send.emit()

    def start_cycle(self, interval_ms: int) -> None:
        self.setInterval(max(10, int(interval_ms)))
        if not self.isActive():
            self.start()

    def stop_cycle(self) -> None:
        if self.isActive():
            self.stop()
