"""发送与调试面板（TX Panel）。

按钮对齐清单：
- [发送数据]：HEX 格式数据发送，带发送日志记录与防呆校验
- [定时循环发送]：复选框 + 间隔 ms（基于 CycleSendTimer/QTimer）
- [清空发送区]：清空发送文本框
- （[清空日志] 与 [复位计数器] 在 LogPanel 里实现，由 MainWindow 跨面板联动）

线程安全：发送调用走 SerialWorker.send，内部 _write_lock 保证原子写入。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QSizePolicy
from qfluentwidgets import (
    CardWidget, BodyLabel, StrongBodyLabel, PushButton, PrimaryPushButton,
    LineEdit, CheckBox, SpinBox, ComboBox,
)

from .workers import SerialWorker, CycleSendTimer


class TxPanel(CardWidget):
    """发送与调试面板。

    信号：
      sendRequested(payload_str, as_text: bool)  : 用户点 [发送数据]
      cycleSendToggled(enabled: bool, interval_ms: int)
    """

    sendRequested = Signal(str, bool)
    cycleSendToggled = Signal(bool, int)
    clearTxFieldRequested = Signal()

    def __init__(self, worker: SerialWorker, cycle_timer: CycleSendTimer, parent=None) -> None:
        super().__init__(parent)
        self._worker = worker
        self._cycle_timer = cycle_timer
        self._build_ui()
        self._connect_signals()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QGridLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        title = StrongBodyLabel("发送与调试", self)
        layout.addWidget(title, 0, 0, 1, 4)

        # 发送模式
        layout.addWidget(BodyLabel("发送模式:", self), 1, 0)
        self.send_mode_combo = ComboBox(self)
        self.send_mode_combo.addItem("HEX (十六进制)", "hex")
        self.send_mode_combo.addItem("ASCII (文本)", "ascii")
        layout.addWidget(self.send_mode_combo, 1, 1)

        # 发送数据输入框
        layout.addWidget(BodyLabel("发送数据:", self), 2, 0)
        self.send_input = LineEdit(self)
        self.send_input.setPlaceholderText("例如: A5 A5 03 20 00 00 71  或  hello")
        layout.addWidget(self.send_input, 2, 1, 1, 2)

        self.send_btn = PrimaryPushButton("发送数据", self)
        layout.addWidget(self.send_btn, 2, 3)

        self.clear_field_btn = PushButton("清空", self)
        layout.addWidget(self.clear_field_btn, 3, 3)

        # 定时循环发送
        self.cycle_check = CheckBox("定时循环发送", self)
        layout.addWidget(self.cycle_check, 3, 0)

        layout.addWidget(BodyLabel("间隔(ms):", self), 3, 1)
        self.interval_spin = SpinBox(self)
        self.interval_spin.setRange(10, 600000)
        self.interval_spin.setValue(1000)
        self.interval_spin.setSingleStep(100)
        layout.addWidget(self.interval_spin, 3, 2)

    def _connect_signals(self) -> None:
        self.send_btn.clicked.connect(self._on_send_clicked)
        self.clear_field_btn.clicked.connect(self._on_clear_field)
        self.cycle_check.toggled.connect(self._on_cycle_toggled)
        self.interval_spin.valueChanged.connect(self._on_interval_changed)
        # 回车直接发送
        self.send_input.returnPressed.connect(self._on_send_clicked)

    # ------------------------------------------------------------------ 槽
    def _on_send_clicked(self) -> None:
        text = self.send_input.text().strip()
        if not text:
            return
        as_text = self.send_mode_combo.currentData() == "ascii"
        # 先发信号给 MainWindow 做日志/校验，由 MainWindow 调 worker.send
        self.sendRequested.emit(text, as_text)

    def _on_clear_field(self) -> None:
        self.send_input.clear()
        self.clearTxFieldRequested.emit()

    def _on_cycle_toggled(self, checked: bool) -> None:
        interval_ms = int(self.interval_spin.value())
        if checked:
            self._cycle_timer.start_cycle(interval_ms)
        else:
            self._cycle_timer.stop_cycle()
        self.cycleSendToggled.emit(checked, interval_ms)
        # 间隔输入框在循环中也可调（_on_interval_changed 会更新 timer）

    def _on_interval_changed(self, val: int) -> None:
        if self.cycle_check.isChecked():
            self._cycle_timer.start_cycle(int(val))
            self.cycleSendToggled.emit(True, int(val))

    # ------------------------------------------------------------------ 外部
    def trigger_send_now(self) -> None:
        """由 CycleSendTimer.triggered_send 触发：按当前内容发送一次。"""
        self._on_send_clicked()

    def fill_payload(self, payload: str, mode: str = "hex") -> None:
        """从命令库 / 历史等外部源一键填入。"""
        self.send_input.setText(payload)
        idx = 0 if mode == "hex" else 1
        self.send_mode_combo.setCurrentIndex(idx)

    def current_state(self) -> dict:
        return {
            "send_mode": self.send_mode_combo.currentData(),
            "payload": self.send_input.text(),
            "cycle_enabled": self.cycle_check.isChecked(),
            "interval_ms": int(self.interval_spin.value()),
        }

    def apply_state(self, state: dict) -> None:
        mode = state.get("send_mode", "hex")
        idx = 0 if mode == "hex" else 1
        self.send_mode_combo.setCurrentIndex(idx)
        if state.get("payload"):
            self.send_input.setText(str(state["payload"]))
        if "interval_ms" in state:
            self.interval_spin.setValue(int(state["interval_ms"]))
        # 注意：不自动启动 cycle，由 MainWindow 在恢复会话时统一处理
