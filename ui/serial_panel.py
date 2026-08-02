"""串口配置面板（Serial Panel）。

按钮对齐清单：
- [刷新串口]：刷新串口列表下拉框
- [打开/关闭串口]：切换串口连接状态，带连接状态指示与文本切换
- [波特率/高级参数]：波特率、数据位、停止位、校验位下拉选择

仅做 UI 与信号槽绑定，不重写任何串口通信核心类逻辑。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QGridLayout, QSizePolicy, QLabel
from qfluentwidgets import (
    CardWidget, BodyLabel, ComboBox, PushButton, PrimaryPushButton,
    StrongBodyLabel,
)

from .workers import SerialWorker


# 标准串口参数选项（与旧版 gui.py 对齐）
BAUDRATES = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]
BYTESIZES = [5, 6, 7, 8]
STOPBITS = [1, 1.5, 2]
PARITIES = ["无 (None)", "偶 (Even)", "奇 (Odd)", "标记 (Mark)", "空格 (Space)"]


class SerialPanel(CardWidget):
    """串口配置面板。

    信号：
      refreshRequested()                : 用户点 [刷新串口]
      openRequested(port, baud, bytesize, stopbits, parity_idx)
      closeRequested()
      configChanged(baudrate, bytesize, stopbits, parity_idx)
    """

    refreshRequested = Signal()
    openRequested = Signal(str, int, int, float, int)
    closeRequested = Signal()
    configChanged = Signal(int, int, float, int)

    def __init__(self, worker: SerialWorker, parent=None) -> None:
        super().__init__(parent)
        self._worker = worker
        self._is_open = False
        self._build_ui()
        self._connect_signals()
        # 首次自动刷新一次（不阻塞 UI：list_ports 在系统层很快）
        self.refresh_ports()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QGridLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = StrongBodyLabel("串口配置", self)
        layout.addWidget(title, 0, 0, 1, 4)

        # 串口下拉 + 刷新
        layout.addWidget(BodyLabel("串口号:", self), 1, 0)
        self.port_combo = ComboBox(self)
        self.port_combo.setMinimumWidth(180)
        self.port_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        layout.addWidget(self.port_combo, 1, 1)

        self.refresh_btn = PushButton("刷新串口", self)
        layout.addWidget(self.refresh_btn, 1, 2)

        # 状态指示 + 开关
        self.status_label = QLabel("● 未连接", self)
        self.status_label.setStyleSheet("color: #9CA3AF; font-weight: 600;")
        layout.addWidget(self.status_label, 1, 3)

        self.connect_btn = PrimaryPushButton("打开串口", self)
        layout.addWidget(self.connect_btn, 2, 3)

        # 波特率
        layout.addWidget(BodyLabel("波特率:", self), 2, 0)
        self.baud_combo = ComboBox(self)
        for b in BAUDRATES:
            self.baud_combo.addItem(str(b), b)
        self.baud_combo.setCurrentText("9600")
        layout.addWidget(self.baud_combo, 2, 1)

        # 高级参数：数据位 / 停止位 / 校验位
        layout.addWidget(BodyLabel("数据位:", self), 3, 0)
        self.bytesize_combo = ComboBox(self)
        for bs in BYTESIZES:
            self.bytesize_combo.addItem(str(bs), bs)
        self.bytesize_combo.setCurrentText("8")
        layout.addWidget(self.bytesize_combo, 3, 1)

        layout.addWidget(BodyLabel("停止位:", self), 3, 2)
        self.stopbits_combo = ComboBox(self)
        for sb in STOPBITS:
            self.stopbits_combo.addItem(str(sb), sb)
        self.stopbits_combo.setCurrentText("1")
        layout.addWidget(self.stopbits_combo, 3, 3)

        layout.addWidget(BodyLabel("校验位:", self), 4, 0)
        self.parity_combo = ComboBox(self)
        for p in PARITIES:
            self.parity_combo.addItem(p)
        layout.addWidget(self.parity_combo, 4, 1, 1, 3)

    def _connect_signals(self) -> None:
        self.refresh_btn.clicked.connect(self.refresh_ports)
        self.refresh_btn.clicked.connect(self.refreshRequested.emit)
        self.connect_btn.clicked.connect(self._on_connect_clicked)

        # 参数变化通知（用于持久化到会话快照）
        self.baud_combo.currentIndexChanged.connect(self._emit_config_changed)
        self.bytesize_combo.currentIndexChanged.connect(self._emit_config_changed)
        self.stopbits_combo.currentIndexChanged.connect(self._emit_config_changed)
        self.parity_combo.currentIndexChanged.connect(self._emit_config_changed)

    # ------------------------------------------------------------------ 槽
    def refresh_ports(self) -> None:
        """[刷新串口]：从 SerialWorker 拉取最新串口列表。"""
        try:
            ports = self._worker.list_ports()
        except Exception:
            ports = []
        cur = self.port_combo.currentText()
        self.port_combo.clear()
        for p in ports:
            label = f"{p['device']}  -  {p.get('description', '')}"
            self.port_combo.addItem(label, p["device"])
        # 尝试恢复原选择
        if cur:
            for i in range(self.port_combo.count()):
                if self.port_combo.itemData(i) == cur or cur in self.port_combo.itemText(i):
                    self.port_combo.setCurrentIndex(i)
                    break

    def _on_connect_clicked(self) -> None:
        if self._is_open:
            self.closeRequested.emit()
        else:
            port = self.port_combo.currentData() or self.port_combo.currentText().split("  ")[0]
            if not port:
                return
            baud = self.baud_combo.currentData() or 9600
            bytesize = self.bytesize_combo.currentData() or 8
            stopbits = self.stopbits_combo.currentData() or 1.0
            parity_idx = self.parity_combo.currentIndex()
            self.openRequested.emit(port, int(baud), int(bytesize), float(stopbits), int(parity_idx))

    def _emit_config_changed(self) -> None:
        self.configChanged.emit(
            int(self.baud_combo.currentData() or 9600),
            int(self.bytesize_combo.currentData() or 8),
            float(self.stopbits_combo.currentData() or 1.0),
            int(self.parity_combo.currentIndex()),
        )

    # ------------------------------------------------------------------ 状态
    def set_connected(self, connected: bool) -> None:
        """外部根据 SerialWorker 的 portOpened / portClosed 信号回调。"""
        self._is_open = connected
        if connected:
            self.connect_btn.setText("关闭串口")
            self.status_label.setText("● 已连接")
            self.status_label.setStyleSheet("color: #0F7B0F; font-weight: 600;")
        else:
            self.connect_btn.setText("打开串口")
            self.status_label.setText("● 未连接")
            self.status_label.setStyleSheet("color: #9CA3AF; font-weight: 600;")
        # 切换串口选择可用性
        self.port_combo.setEnabled(not connected)
        self.baud_combo.setEnabled(not connected)
        self.bytesize_combo.setEnabled(not connected)
        self.stopbits_combo.setEnabled(not connected)
        self.parity_combo.setEnabled(not connected)
        self.refresh_btn.setEnabled(not connected)

    # ------------------------------------------------------------------ 配置读写
    def current_config(self) -> dict:
        return {
            "port": self.port_combo.currentData() or "",
            "baudrate": int(self.baud_combo.currentData() or 9600),
            "bytesize": int(self.bytesize_combo.currentData() or 8),
            "stopbits": float(self.stopbits_combo.currentData() or 1.0),
            "parity_idx": int(self.parity_combo.currentIndex()),
        }

    def apply_config(self, cfg: dict) -> None:
        port = cfg.get("port", "")
        if port:
            for i in range(self.port_combo.count()):
                if self.port_combo.itemData(i) == port:
                    self.port_combo.setCurrentIndex(i)
                    break
        baud = cfg.get("baudrate", 9600)
        for i in range(self.baud_combo.count()):
            if self.baud_combo.itemData(i) == baud:
                self.baud_combo.setCurrentIndex(i)
                break
        bs = cfg.get("bytesize", 8)
        for i in range(self.bytesize_combo.count()):
            if self.bytesize_combo.itemData(i) == bs:
                self.bytesize_combo.setCurrentIndex(i)
                break
        sb = cfg.get("stopbits", 1.0)
        for i in range(self.stopbits_combo.count()):
            if self.stopbits_combo.itemData(i) == sb:
                self.stopbits_combo.setCurrentIndex(i)
                break
        pi = cfg.get("parity_idx", 0)
        if 0 <= pi < self.parity_combo.count():
            self.parity_combo.setCurrentIndex(pi)
