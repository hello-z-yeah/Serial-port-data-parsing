"""监听工具页面。

页面提供独立的实时数据窗口和监听记录窗口：
- 左侧实时数据窗口保留 HEX/ASCII 切换、清空、自动滚动按钮。
- 右侧监听记录窗口显示匹配到指定内容的完整数据及时间戳。
- 设置监听按钮可配置 HEX 或 ASCII 匹配内容。
"""
from __future__ import annotations

import re
from datetime import datetime

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLineEdit,
    QMessageBox,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    ToggleButton,
)

from protocol_parser.mcu_page import CtrlWheelZoomTextEdit
from protocol_parser.dpi_font import UI_FONT_BASE_POINT_SIZE
from protocol_parser.log_text_style import apply_log_text_edit_style
from protocol_parser.monitor_settings import (
    MAX_MONITOR_PATTERN_CHARS,
    load_monitor_settings,
    sanitize_monitor_settings,
    save_monitor_settings,
)
from protocol_parser.widgets import apply_tooltip


class _MonitorSettingsDialog(QDialog):
    """设置监听内容的简单对话框。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置监听内容")
        self.setMinimumWidth(360)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        layout.addWidget(BodyLabel("匹配内容：", self))
        self.pattern_edit = QLineEdit(self)
        self.pattern_edit.setPlaceholderText("例如 ASCII: FW_NAME  或 HEX: A5 A5")
        layout.addWidget(self.pattern_edit)

        self.hex_check = CheckBox("HEX 格式（按十六进制字节匹配）", self)
        layout.addWidget(self.hex_check)

        button_layout = QHBoxLayout()
        button_layout.addStretch(1)
        self.btn_ok = PrimaryPushButton("确定", self)
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel = PushButton("取消", self)
        self.btn_cancel.clicked.connect(self.reject)
        button_layout.addWidget(self.btn_ok)
        button_layout.addWidget(self.btn_cancel)
        layout.addLayout(button_layout)

    def get_values(self) -> tuple[str, bool]:
        return self.pattern_edit.text().strip(), self.hex_check.isChecked()


class MonitorToolPage(QWidget):
    """左侧导航页：监听工具。"""

    def __init__(self, main_window: QWidget) -> None:
        super().__init__(main_window)
        self.setObjectName("monitorToolPage")
        self._mw = main_window

        settings = load_monitor_settings()
        self._monitor_pattern_text: str = str(settings["pattern_text"])
        self._monitor_pattern_bytes: bytes | None = None
        self._monitor_is_hex: bool = bool(settings["is_hex"])
        self._max_record_lines = int(settings["max_record_lines"])
        self.autoscroll = bool(settings["autoscroll"])
        self.hex_format = bool(settings["hex_format"])

        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 2, 0, 0)
        self._root.setSpacing(6)

        # 顶部标题栏
        switch_card = QWidget(self)
        self.switch_card = switch_card
        switch_layout = QHBoxLayout(switch_card)
        switch_layout.setContentsMargins(12, 7, 12, 7)
        switch_layout.setSpacing(8)
        switch_layout.addWidget(StrongBodyLabel("监听工具", switch_card))
        switch_layout.addStretch(1)

        self.btn_settings = PushButton("设置监听", switch_card)
        self.btn_settings.clicked.connect(self._on_settings)
        apply_tooltip(self.btn_settings, "设置要监听的 HEX 或 ASCII 内容")
        switch_layout.addWidget(self.btn_settings)

        self._root.addWidget(switch_card)

        # 主内容区
        content = QWidget(self)
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        self._root.addWidget(content, 1)

        splitter = QSplitter(Qt.Orientation.Horizontal, content)
        splitter.setObjectName("monitorToolSplitter")
        splitter.setHandleWidth(6)
        splitter.setChildrenCollapsible(False)

        self.realtime_card = self._build_realtime_card()
        self.record_card = self._build_record_card()

        self.realtime_card.setMinimumWidth(300)
        self.record_card.setMinimumWidth(260)
        self.realtime_card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        self.record_card.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )

        splitter.addWidget(self.realtime_card)
        splitter.addWidget(self.record_card)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([900, 460])

        self.content_layout.addWidget(splitter, 1)
        self.import_settings(settings, persist=False)

    def _build_realtime_card(self) -> CardWidget:
        """左侧：实时数据窗口（保留 HEX/ASCII、清空、自动滚动）。"""
        card = CardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        toolbar = QWidget(card)
        toolbar_layout = QHBoxLayout(toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        toolbar_layout.setSpacing(8)

        self.btn_hex = ToggleButton("HEX格式")
        self.btn_hex.setChecked(self.hex_format)
        apply_tooltip(self.btn_hex, "蓝色：HEX格式；白色：ASCII格式")
        self.btn_hex.toggled.connect(self._on_hex_toggled)
        toolbar_layout.addWidget(self.btn_hex)

        self.btn_clear = PushButton("清空")
        self.btn_clear.clicked.connect(self._clear_output)
        toolbar_layout.addWidget(self.btn_clear)

        self.btn_autoscroll = ToggleButton("自动滚动")
        self.btn_autoscroll.setChecked(self.autoscroll)
        self.btn_autoscroll.toggled.connect(self._on_autoscroll_toggled)
        toolbar_layout.addWidget(self.btn_autoscroll)

        toolbar_layout.addStretch(1)
        layout.addWidget(toolbar)

        self.serial_text = CtrlWheelZoomTextEdit()
        self.serial_text.setObjectName("MonitorRealtimeDataText")
        self.serial_text.setReadOnly(True)
        self.serial_text.setUndoRedoEnabled(False)
        self.serial_text.setAcceptRichText(False)
        self.serial_text.setLineWrapMode(CtrlWheelZoomTextEdit.WidgetWidth)
        self.serial_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.serial_text.document().setMaximumBlockCount(20000)
        self._apply_text_style(self.serial_text)
        layout.addWidget(self.serial_text, stretch=1)

        return card

    def _build_record_card(self) -> CardWidget:
        """右侧：监听记录窗口。"""
        card = CardWidget()
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        header = QWidget(card)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)
        header_layout.addWidget(StrongBodyLabel("监听记录", card))
        header_layout.addStretch(1)

        self.btn_export_records = PushButton("导出监听记录")
        self.btn_export_records.clicked.connect(self._export_records)
        apply_tooltip(self.btn_export_records, "导出监听记录为 Word 文档")
        header_layout.addWidget(self.btn_export_records)

        self.btn_clear_records = PushButton("清空记录")
        self.btn_clear_records.clicked.connect(self._clear_records)
        header_layout.addWidget(self.btn_clear_records)
        layout.addWidget(header)

        self.record_text = CtrlWheelZoomTextEdit()
        self.record_text.setObjectName("MonitorRecordText")
        self.record_text.setReadOnly(True)
        self.record_text.setUndoRedoEnabled(False)
        self.record_text.setAcceptRichText(False)
        self.record_text.setLineWrapMode(CtrlWheelZoomTextEdit.WidgetWidth)
        self.record_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.record_text.document().setMaximumBlockCount(self._max_record_lines)
        self._apply_text_style(self.record_text)
        layout.addWidget(self.record_text, stretch=1)

        return card

    def _apply_text_style(self, text_edit: CtrlWheelZoomTextEdit) -> None:
        """应用与主窗口实时数据框一致的字体和边框样式。"""
        apply_log_text_edit_style(
            text_edit,
            point_size=UI_FONT_BASE_POINT_SIZE,
        )

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------

    def _on_hex_toggled(self, checked: bool) -> None:
        self.hex_format = bool(checked)
        self._save_settings()

    def _on_autoscroll_toggled(self, checked: bool) -> None:
        self.autoscroll = bool(checked)
        self._save_settings()

    def export_settings(self) -> dict:
        return {
            "pattern_text": self._monitor_pattern_text,
            "is_hex": self._monitor_is_hex,
            "hex_format": self.hex_format,
            "autoscroll": self.autoscroll,
            "max_record_lines": self._max_record_lines,
        }

    def import_settings(self, payload: dict, *, persist: bool = False) -> None:
        settings = sanitize_monitor_settings(payload)
        self._max_record_lines = int(settings["max_record_lines"])
        if hasattr(self, "record_text"):
            self.record_text.document().setMaximumBlockCount(self._max_record_lines)
        self.hex_format = bool(settings["hex_format"])
        self.autoscroll = bool(settings["autoscroll"])
        for button_name, checked in (
            ("btn_hex", self.hex_format),
            ("btn_autoscroll", self.autoscroll),
        ):
            button = getattr(self, button_name, None)
            if button is not None:
                button.blockSignals(True)
                button.setChecked(checked)
                button.blockSignals(False)
        if not self._set_monitor_pattern(
            str(settings["pattern_text"]),
            bool(settings["is_hex"]),
            show_error=False,
        ):
            self._set_monitor_pattern("", False, show_error=False)
        if persist:
            self._save_settings(show_error=True)

    def _save_settings(self, *, show_error: bool = False) -> None:
        try:
            save_monitor_settings(self.export_settings())
        except OSError as exc:
            if show_error:
                QMessageBox.warning(self, "保存监听设置失败", str(exc))

    def _set_monitor_pattern(
        self,
        text: str,
        is_hex: bool,
        *,
        show_error: bool,
    ) -> bool:
        text = str(text or "").strip()
        if len(text) > MAX_MONITOR_PATTERN_CHARS:
            if show_error:
                QMessageBox.warning(
                    self,
                    "监听内容过长",
                    f"监听内容最多允许 {MAX_MONITOR_PATTERN_CHARS} 个字符。",
                )
            return False
        pattern: bytes | None = None
        if text:
            if is_hex:
                try:
                    cleaned = re.sub(r"[^0-9a-fA-F]", "", text)
                    if not cleaned or len(cleaned) % 2 != 0:
                        raise ValueError("HEX 字节数必须为非零偶数")
                    pattern = bytes.fromhex(cleaned)
                except ValueError as exc:
                    if show_error:
                        QMessageBox.warning(
                            self,
                            "HEX 格式错误",
                            f"无法解析监听内容：{exc}",
                        )
                    return False
            else:
                pattern = text.encode("utf-8")
        self._monitor_pattern_text = text
        self._monitor_is_hex = bool(is_hex and text)
        self._monitor_pattern_bytes = pattern
        return True

    def _clear_output(self) -> None:
        self.serial_text.clear()

    def _clear_records(self) -> None:
        self.record_text.clear()

    def _export_records(self) -> None:
        """将监听记录导出为 Word 文档。"""
        text = self.record_text.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "无记录", "当前没有监听记录可导出。")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "导出监听记录", "监听记录.docx", "Word 文档 (*.docx)"
        )
        if not path:
            return

        try:
            from docx import Document
            doc = Document()
            doc.add_heading("监听记录", level=1)
            for line in text.splitlines():
                if line.strip():
                    doc.add_paragraph(line)
            doc.save(path)
            QMessageBox.information(self, "导出成功", f"已保存到：{path}")
        except Exception as exc:
            QMessageBox.warning(self, "导出失败", f"导出 Word 失败：{exc}")

    def _on_settings(self) -> None:
        dialog = _MonitorSettingsDialog(self)
        dialog.pattern_edit.setText(self._monitor_pattern_text)
        dialog.hex_check.setChecked(self._monitor_is_hex)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        text, is_hex = dialog.get_values()
        if self._set_monitor_pattern(text, is_hex, show_error=True):
            self._save_settings(show_error=True)

    # ------------------------------------------------------------------
    # 数据显示与监听匹配
    # ------------------------------------------------------------------

    def display_raw_data(self, data: bytes, ts: float) -> None:
        """显示原始 RX 数据，并检查是否命中监听内容。"""
        ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        self._append_to_text(self.serial_text, self._format_data_line(data, ts_str))
        self._check_and_record(data, ts)

    def display_tx(self, data: bytes, ts: float) -> None:
        """显示 TX 数据（监听匹配只针对 RX，这里仅用于实时数据窗口显示）。"""
        ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        raw_label = "Raw-HEX" if self.hex_format else "Raw-ASCII"
        line = f"[{ts_str}] [TX] {raw_label} {self._format_data(data)}\n"
        self._append_to_text(self.serial_text, line)

    def clear_output(self) -> None:
        self._clear_output()

    def export_records(self) -> None:
        self._export_records()

    def _check_and_record(self, data: bytes, ts: float) -> None:
        """如果数据包含监听内容，在右侧记录完整数据和时间戳，并把匹配内容标红。"""
        pattern = self._monitor_pattern_bytes
        if not pattern:
            return

        matched = pattern in data
        if not matched and not self._monitor_is_hex:
            # ASCII 模式也尝试在 UTF-8 解码后的文本中匹配。
            try:
                matched = self._monitor_pattern_text in data.decode("utf-8")
            except UnicodeDecodeError:
                pass

        if matched:
            ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
            if self._monitor_is_hex:
                shown = " ".join(f"{byte:02X}" for byte in data)
                highlighted = " ".join(f"{byte:02X}" for byte in pattern)
            else:
                shown = self._format_data(data)
                highlighted = self._monitor_pattern_text
            line = f"[{ts_str}] {shown}\n"
            highlight = [highlighted]
            self._append_to_text(self.record_text, line, highlight_terms=highlight)

    def _format_data_line(self, data: bytes, ts_str: str) -> str:
        raw_label = "Raw-HEX" if self.hex_format else "Raw-ASCII"
        return f"[{ts_str}] [RX] {raw_label} {self._format_data(data)}\n"

    def _format_data(self, data: bytes) -> str:
        if self.hex_format:
            return " ".join(f"{b:02X}" for b in data)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return " ".join(f"{b:02X}" for b in data)
        # 不在界面显示 \r\n 等转义符号，统一替换为空格。
        return text.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")

    _SEGMENT_RE = re.compile(
        r"(\[\d{2}:\d{2}:\d{2}\.\d{3}\])|"
        r"(\[\s*(?:EMERG|ERROR|WARN|NOTICE|INFO|DEBUG|TRACE)\s*\])|"
        r"(\[(TX|RX)\])|"
        r"(Raw-(?:ASCII|HEX))"
    )

    _LEVEL_STYLES = {
        "EMERG":  ("#C42B1C", "#FDE9E7"),
        "ERROR":  ("#C42B1C", "#FDE9E7"),
        "WARN":   ("#E65100", "#FFF3E0"),
        "NOTICE": ("#0066CC", "#E3F2FD"),
        "INFO":   ("#374151", "#E8E8E8"),
        "DEBUG":  ("#607D8B", "#ECEFF1"),
        "TRACE":  ("#607D8B", "#ECEFF1"),
    }

    def _append_to_text(
        self,
        text_edit: CtrlWheelZoomTextEdit,
        text: str,
        highlight_terms: list[str] | None = None,
    ) -> None:
        """追加文本并给时间戳、级别标签、TX/RX、Raw 标签上色/加背景；
        同时把 highlight_terms 中的文本标红（仅在普通文本段中匹配）。"""
        if not text:
            return

        terms = [t for t in (highlight_terms or []) if t]

        cursor = QTextCursor(text_edit.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)

        current_color: str | None = None
        current_bg: str | None = None

        def _apply(color: str | None, bg: str | None) -> None:
            nonlocal current_color, current_bg
            if color == current_color and bg == current_bg:
                return
            fmt = QTextCharFormat()
            if color:
                fmt.setForeground(QColor(color))
            if bg:
                fmt.setBackground(QColor(bg))
            cursor.setCharFormat(fmt)
            current_color = color
            current_bg = bg

        def _insert_plain(part: str, color: str | None, bg: str | None) -> None:
            """插入普通文本，并把命中关键词标红。"""
            if not terms:
                _apply(color, bg)
                cursor.insertText(part)
                return
            pos = 0
            while pos < len(part):
                earliest: tuple[int, str] | None = None
                for term in terms:
                    idx = part.find(term, pos)
                    if idx != -1 and (earliest is None or idx < earliest[0]):
                        earliest = (idx, term)
                if earliest is None:
                    if pos < len(part):
                        _apply(color, bg)
                        cursor.insertText(part[pos:])
                    break
                start, term = earliest
                if start > pos:
                    _apply(color, bg)
                    cursor.insertText(part[pos:start])
                _apply("#FF0000", bg)
                cursor.insertText(term)
                pos = start + len(term)

        pos = 0
        for match in self._SEGMENT_RE.finditer(text):
            if match.start() > pos:
                _insert_plain(text[pos:match.start()], "#0000CD", None)

            if match.group(1):  # 时间戳
                _apply("#2E86FF", "#FFF9C4")
            elif match.group(2):  # [DEBUG] / [INFO] 等级别标签
                level = match.group(2).strip("[] ").upper()
                fg, bg = self._LEVEL_STYLES.get(level, ("#374151", "#E8E8E8"))
                _apply(fg, bg)
            elif match.group(3):  # [TX] / [RX]
                if match.group(4).upper() == "TX":
                    _apply("#008000", "#E8F5E9")
                else:
                    _apply("#0000CD", "#E3F2FD")
            else:  # Raw-ASCII / Raw-HEX
                _apply("#008000", "#E8F5E9")

            cursor.insertText(match.group(0))
            pos = match.end()

        if pos < len(text):
            _insert_plain(text[pos:], "#0000CD", None)

        if getattr(self, "autoscroll", True):
            text_edit.setTextCursor(cursor)
            text_edit.ensureCursorVisible()
