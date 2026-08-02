"""协议解析面板（Parser Panel）。

按钮对齐清单：
- [手动解析按钮]：触发当前 HEX 数据的协议层解析
- [300ms 防抖自动解析]：输入框变化时通过 QTimer 300ms 延迟自动预解析
- [校验和追加]：支持配置并自动计算/追加 ADD8/XOR8/CRC16/CRC32 等校验位

业务逻辑零修改 —— 调用 protocol_parser.parse_frame / calc_checksum 即可，
本面板只负责 UI 与防抖 QTimer 的管理。
"""
from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QObject, Signal, QTimer, Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QSizePolicy
from qfluentwidgets import (
    CardWidget, BodyLabel, StrongBodyLabel, PushButton, PrimaryPushButton,
    PlainTextEdit, ComboBox, CheckBox,
)


# 校验和算法选项（与 parser.calc_checksum 完全对齐）
CHECKSUM_ALGOS = [
    ("不追加", ""),
    ("ADD8 (求和)", "add8"),
    ("XOR8 (异或)", "xor8"),
    ("CRC8", "crc8"),
    ("ADD16 (求和16)", "add16"),
    ("CRC16-Modbus", "modbuscrc16"),
    ("CRC16-CCITT", "ccittcrc16"),
    ("CRC32", "crc32"),
]


class ParserPanel(CardWidget):
    """协议解析面板。

    信号：
      parseRequested(hex_str: str)           : 用户点 [手动解析] 或防抖触发
      appendChecksumRequested(hex_str, algo) : 用户点 [追加校验]
    """

    parseRequested = Signal(str)
    appendChecksumRequested = Signal(str, str)

    DEBOUNCE_MS = 300  # 防抖 300ms

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._build_ui()
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(self.DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._on_debounce_timeout)
        self._connect_signals()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QGridLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        title = StrongBodyLabel("协议解析", self)
        layout.addWidget(title, 0, 0, 1, 4)

        # HEX 输入框
        layout.addWidget(BodyLabel("HEX 数据:", self), 1, 0)
        self.hex_input = PlainTextEdit(self)
        self.hex_input.setPlaceholderText("粘贴 hex 字节，例如: A5 A5 03 20 00 00 71")
        self.hex_input.setMaximumHeight(80)
        layout.addWidget(self.hex_input, 1, 1, 1, 3)

        # 操作行
        self.parse_btn = PrimaryPushButton("手动解析", self)
        layout.addWidget(self.parse_btn, 2, 0)

        self.auto_check = CheckBox("300ms 防抖自动解析", self)
        self.auto_check.setChecked(True)
        layout.addWidget(self.auto_check, 2, 1)

        # 校验和
        layout.addWidget(BodyLabel("校验和算法:", self), 3, 0)
        self.checksum_combo = ComboBox(self)
        for label, algo in CHECKSUM_ALGOS:
            self.checksum_combo.addItem(label, algo)
        layout.addWidget(self.checksum_combo, 3, 1)

        self.append_checksum_btn = PushButton("追加校验", self)
        layout.addWidget(self.append_checksum_btn, 3, 2)

    def _connect_signals(self) -> None:
        self.parse_btn.clicked.connect(self._on_manual_parse)
        self.append_checksum_btn.clicked.connect(self._on_append_checksum)
        # 输入框文本变化 → 触发防抖
        self.hex_input.textChanged.connect(self._on_text_changed)

    # ------------------------------------------------------------------ 槽
    def _on_text_changed(self) -> None:
        if self.auto_check.isChecked():
            self._debounce_timer.start()  # 重置 300ms 计时

    def _on_debounce_timeout(self) -> None:
        text = self.hex_input.toPlainText().strip()
        if text:
            self.parseRequested.emit(text)

    def _on_manual_parse(self) -> None:
        text = self.hex_input.toPlainText().strip()
        if text:
            self.parseRequested.emit(text)

    def _on_append_checksum(self) -> None:
        text = self.hex_input.toPlainText().strip()
        algo = self.checksum_combo.currentData() or ""
        if not text or not algo:
            return
        self.appendChecksumRequested.emit(text, algo)

    # ------------------------------------------------------------------ 外部
    def set_hex_text(self, text: str) -> None:
        """外部一键填入（来自命令库 / 接收区）。"""
        self.hex_input.setPlainText(text)

    def current_hex(self) -> str:
        return self.hex_input.toPlainText().strip()

    def current_checksum_algo(self) -> str:
        return self.checksum_combo.currentData() or ""
