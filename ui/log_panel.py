"""RX/TX 日志显示面板（Log Panel）。

- 接收区：彩色显示 RX（绿色）/ TX（蓝色）HEX 文本与时间戳
- 解析结果：实时显示命令字、字段、校验状态
- RX/TX 计数器：累计接收/发送字节数，支持一键复位
- 清空日志：清空文本框内容（不影响计数器）
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QTextCharFormat, QTextCursor, QColor, QFont
from PySide6.QtWidgets import QTextEdit
from qfluentwidgets import (
    CardWidget, StrongBodyLabel, PushButton, BodyLabel,
    TextEdit, SimpleCardWidget,
)


def _ts_str(ts: Optional[float] = None) -> str:
    if ts is None:
        from time import time
        ts = time()
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]


class LogPanel(CardWidget):
    """日志显示面板（接收区 + 计数器 + 清空按钮）。"""

    clearRequested = Signal()

    # 颜色（与 theme.PALETTE 对齐）
    _COLOR_TS = "#8A9099"
    _COLOR_RX = "#0A7A5A"
    _COLOR_TX = "#1A56DB"
    _COLOR_ERR = "#C42B1C"
    _COLOR_FIELD = "#374151"
    _COLOR_RAW = "#6B7280"

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rx_bytes = 0
        self._tx_bytes = 0
        self._build_ui()

    def _build_ui(self) -> None:
        from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # 标题行
        head = QHBoxLayout()
        head.addWidget(StrongBodyLabel("收发日志", self))
        head.addStretch(1)
        self.rx_counter_label = BodyLabel("RX: 0 B", self)
        self.tx_counter_label = BodyLabel("TX: 0 B", self)
        head.addWidget(self.rx_counter_label)
        head.addWidget(self.tx_counter_label)
        self.reset_counter_btn = PushButton("复位计数器", self)
        self.reset_counter_btn.clicked.connect(self.reset_counters)
        head.addWidget(self.reset_counter_btn)
        self.clear_log_btn = PushButton("清空日志", self)
        self.clear_log_btn.clicked.connect(self.clear_logs)
        head.addWidget(self.clear_log_btn)
        layout.addLayout(head)

        # 文本显示
        self.text_edit = TextEdit(self)
        self.text_edit.setReadOnly(True)
        self.text_edit.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        # 等宽字体
        font = QFont("Consolas", 9)
        font.setStyleHint(QFont.StyleHint.Monospace)
        self.text_edit.setFont(font)
        layout.addWidget(self.text_edit, 1)

    # ------------------------------------------------------------------ 写入
    def _append(self, text: str, color: str) -> None:
        cursor = self.text_edit.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        fmt = QTextCharFormat()
        fmt.setForeground(QColor(color))
        cursor.setCharFormat(fmt)
        cursor.insertText(text)
        self.text_edit.setTextCursor(cursor)
        self.text_edit.verticalScrollBar().setValue(self.text_edit.verticalScrollBar().maximum())

    def append_rx_raw(self, data: bytes, ts: Optional[float] = None) -> None:
        """RX 原始字节（ASCII / 无协议模式）。"""
        self._rx_bytes += len(data)
        self._update_counters()
        hex_str = " ".join(f"{b:02X}" for b in data)
        # 尝试显示 ASCII（可打印部分）
        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in data)
        self._append(f"[{_ts_str(ts)}] RX ", self._COLOR_TS)
        self._append(f"{hex_str}", self._COLOR_RX)
        self._append(f"  | {ascii_str}\n", self._COLOR_RAW)

    def append_tx(self, data: bytes, ts: Optional[float] = None) -> None:
        """TX 字节写入。"""
        self._tx_bytes += len(data)
        self._update_counters()
        hex_str = " ".join(f"{b:02X}" for b in data)
        self._append(f"[{_ts_str(ts)}] TX ", self._COLOR_TS)
        self._append(f"{hex_str}\n", self._COLOR_TX)

    def append_frame_result(self, result, ts: Optional[float] = None) -> None:
        """协议解析结果（ParseResult）。"""
        if ts is None:
            from time import time
            ts = time()
        # 标题行
        cs_flag = ""
        if result.checksum_ok is True:
            cs_flag = " [校验✓]"
        elif result.checksum_ok is False:
            cs_flag = " [校验✗]"
        if result.error:
            self._append(f"[{_ts_str(ts)}] ERR ", self._COLOR_ERR)
            self._append(f"{result.cmd_code} {result.cmd_name}: {result.error}\n", self._COLOR_ERR)
            return
        self._append(f"[{_ts_str(ts)}] ", self._COLOR_TS)
        self._append(f"{result.cmd_code} {result.cmd_name}", self._COLOR_RX)
        if result.direction:
            self._append(f"  [{result.direction}]", self._COLOR_FIELD)
        self._append(f"{cs_flag}  | {result.raw_hex}\n", self._COLOR_RAW)
        # 字段
        for f in result.fields or []:
            name = f.get("name", "")
            text = f.get("text", "")
            if not name or f.get("type") == "separator":
                continue
            self._append(f"    · {name}: {text}\n", self._COLOR_FIELD)

    def append_error(self, msg: str) -> None:
        self._append(f"[{_ts_str()}] {msg}\n", self._COLOR_ERR)

    # ------------------------------------------------------------------ 计数器
    def _update_counters(self) -> None:
        self.rx_counter_label.setText(f"RX: {self._format_bytes(self._rx_bytes)}")
        self.tx_counter_label.setText(f"TX: {self._format_bytes(self._tx_bytes)}")

    @staticmethod
    def _format_bytes(n: int) -> str:
        if n < 1024:
            return f"{n} B"
        if n < 1024 * 1024:
            return f"{n/1024:.2f} KB"
        return f"{n/1024/1024:.2f} MB"

    def reset_counters(self) -> None:
        """[复位计数器]：清空 RX/TX 接收与发送字节累计计数。"""
        self._rx_bytes = 0
        self._tx_bytes = 0
        self._update_counters()

    def clear_logs(self) -> None:
        """[清空接收/发送日志]：清空文本框。"""
        self.text_edit.clear()
        self.clearRequested.emit()

    # ------------------------------------------------------------------ 导出
    def export_lines(self) -> list[str]:
        """导出当前日志的纯文本（用于 Excel/CSV 导出）。"""
        return self.text_edit.toPlainText().splitlines()
