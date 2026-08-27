"""监听工具页面。

页面提供独立的实时数据窗口和监听记录窗口：
- 左侧实时数据窗口保留 HEX/ASCII 切换、清空、自动滚动按钮。
- 右侧监听记录窗口显示匹配到指定内容的完整数据及时间戳。
- 设置监听按钮可配置 HEX 或 ASCII 匹配内容。
"""
from __future__ import annotations

import re
import threading
from collections import deque
from datetime import datetime

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QProgressDialog,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
    QLabel,
)
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    CheckBox,
    LineEdit,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
    ToggleButton,
)

from protocol_parser.mcu_page import CtrlWheelZoomTextEdit
from protocol_parser.display_format import (
    build_receive_color_segments,
    format_monitor_raw_line,
    LEVEL_STYLES,
    normalize_monitor_display_line,
)
from protocol_parser.dpi_font import UI_FONT_BASE_POINT_SIZE, fit_dialog_to_content, fit_text_control
from protocol_parser.log_text_style import apply_log_text_edit_style, reapply_log_text_font
from protocol_parser.monitor_settings import (
    MAX_MONITOR_PATTERN_CHARS,
    load_monitor_settings,
    sanitize_monitor_settings,
    save_monitor_settings,
)
from protocol_parser.widgets import (
    apply_tooltip,
    apply_fluent_dialog_style,
    apply_fluent_progress_dialog_style,
    StyledMessageBox,
)

QMessageBox = StyledMessageBox


class _MonitorSettingsDialog(QDialog):
    """设置监听内容的简单对话框。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        apply_fluent_dialog_style(self)
        self.setWindowTitle("设置监听内容")
        self.setMinimumSize(420, 220)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(0)

        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        layout.addWidget(StrongBodyLabel("设置监听内容", card))
        layout.addWidget(BodyLabel("匹配内容：", card))
        self.pattern_edit = LineEdit(card)
        self.pattern_edit.setPlaceholderText("例如 ASCII: FW_NAME  或 HEX: A5 A5")
        self.pattern_edit.setClearButtonEnabled(True)
        layout.addWidget(self.pattern_edit)

        self.hex_check = CheckBox("HEX 格式（按十六进制字节匹配）", card)
        layout.addWidget(self.hex_check)

        button_layout = QHBoxLayout()
        button_layout.setSpacing(8)
        button_layout.addStretch(1)
        self.btn_cancel = PushButton("取消", card)
        self.btn_cancel.clicked.connect(self.reject)
        fit_text_control(self.btn_cancel, point_size=UI_FONT_BASE_POINT_SIZE)
        button_layout.addWidget(self.btn_cancel)
        self.btn_ok = PrimaryPushButton("确定", card)
        self.btn_ok.clicked.connect(self.accept)
        fit_text_control(self.btn_ok, point_size=UI_FONT_BASE_POINT_SIZE)
        button_layout.addWidget(self.btn_ok)
        layout.addLayout(button_layout)
        outer.addWidget(card)

        fit_dialog_to_content(
            self,
            preferred_width=480,
            minimum=(420, 220),
            margin=(36, 72),
            point_size=UI_FONT_BASE_POINT_SIZE,
        )

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self.property("_smstDialogGeometryPinned"):
            return
        self.setProperty("_smstDialogGeometryPinned", True)
        pinned = self.size()
        if pinned.width() > 0 and pinned.height() > 0:
            self.setMinimumSize(pinned)
            self.setMaximumSize(pinned)

    def get_values(self) -> tuple[str, bool]:
        return self.pattern_edit.text().strip(), self.hex_check.isChecked()


class MonitorToolPage(QWidget):
    """左侧导航页：监听工具。"""

    _export_finished = Signal(str, object)  # path, error (Exception | None)

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
        self._export_progress: QProgressDialog | None = None
        self._export_finished.connect(self._on_export_finished)

        # 顶部标题栏
        switch_card = QWidget(self)
        self.switch_card = switch_card
        switch_layout = QHBoxLayout(switch_card)
        switch_layout.setContentsMargins(12, 7, 12, 7)
        switch_layout.setSpacing(8)
        self.page_title_label = StrongBodyLabel("监听工具", switch_card)
        switch_layout.addWidget(self.page_title_label)
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
        self.serial_text.setProperty("smstIndependentDataFont", True)
        self.serial_text.setObjectName("MonitorRealtimeDataText")
        self.serial_text.setReadOnly(True)
        self.serial_text.setUndoRedoEnabled(False)
        self.serial_text.setAcceptRichText(False)
        self.serial_text.document().setMaximumBlockCount(20000)
        self._apply_text_style(self.serial_text)
        self._realtime_pending: deque[str] = deque()
        self._record_pending: deque[tuple[str, list[str] | None]] = deque()
        self._pending_realtime_chars = 0
        self._log_flush_timer = QTimer(self)
        self._log_flush_timer.setSingleShot(True)
        self._log_flush_timer.setInterval(50)
        self._log_flush_timer.timeout.connect(self._flush_log_batch)
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
        self.record_text.setProperty("smstIndependentDataFont", True)
        self.record_text.setObjectName("MonitorRecordText")
        self.record_text.setReadOnly(True)
        self.record_text.setUndoRedoEnabled(False)
        self.record_text.setAcceptRichText(False)
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

    def reapply_log_fonts(self) -> None:
        """Restore monospace fonts after a global UI font refresh."""
        for name in ("serial_text", "record_text"):
            text_edit = getattr(self, name, None)
            if text_edit is not None:
                reapply_log_text_font(text_edit)

    # ------------------------------------------------------------------
    # 事件处理
    # ------------------------------------------------------------------

    def _on_hex_toggled(self, checked: bool) -> None:
        self.hex_format = bool(checked)
        self._save_settings()
        notify = getattr(self._mw, "_on_monitor_hex_toggled", None)
        if callable(notify):
            notify(bool(checked))

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
        self._realtime_pending.clear()
        self._pending_realtime_chars = 0
        if self._log_flush_timer.isActive():
            self._log_flush_timer.stop()
        self.serial_text.clear()
        self._schedule_main_status_refresh()

    def _clear_records(self) -> None:
        self._record_pending.clear()
        if self._log_flush_timer.isActive() and not self._realtime_pending:
            self._log_flush_timer.stop()
        self.record_text.clear()

    def _export_records(self) -> None:
        """将监听记录导出为 Word 文档（后台线程写入，避免阻塞 UI）。"""
        self.flush_pending_display()
        text = self.record_text.toPlainText()
        if not text.strip():
            QMessageBox.information(self, "无记录", "当前没有监听记录可导出。")
            return

        path, _ = QFileDialog.getSaveFileName(
            self, "导出监听记录", "监听记录.docx", "Word 文档 (*.docx)"
        )
        if not path:
            return

        lines = [line for line in text.splitlines() if line.strip()]
        progress = QProgressDialog("正在导出 Word 文档…", None, 0, 0, self)
        apply_fluent_progress_dialog_style(progress)
        progress.setWindowTitle("导出监听记录")
        progress.setWindowModality(Qt.WindowModality.WindowModal)
        progress.setMinimumDuration(0)
        progress.setCancelButton(None)
        self._export_progress = progress
        progress.show()

        def worker() -> None:
            error: Exception | None = None
            try:
                from docx import Document

                doc = Document()
                doc.add_heading("监听记录", level=1)
                for line in lines:
                    doc.add_paragraph(line)
                doc.save(path)
            except Exception as exc:
                error = exc
            self._export_finished.emit(path, error)

        threading.Thread(
            target=worker,
            daemon=True,
            name="smst-monitor-export",
        ).start()

    def _on_export_finished(
        self,
        path: str,
        error: object,
    ) -> None:
        progress = self._export_progress
        self._export_progress = None
        if progress is not None:
            progress.close()
        if error is not None:
            QMessageBox.warning(self, "导出失败", f"导出 Word 失败：{error}")
            return
        QMessageBox.information(self, "导出成功", f"已保存到：{path}")

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
        line = format_monitor_raw_line(data, ts, hex_format=bool(self.hex_format))
        if line:
            self._enqueue_realtime(line)
        self._check_and_record(data, ts)

    def display_raw_line(self, line: str, data: bytes, ts: float) -> None:
        """Append a preformatted monitor line and run pattern matching."""
        if not line:
            line = format_monitor_raw_line(data, ts, hex_format=bool(self.hex_format))
        if line:
            self._enqueue_realtime(line)
        self._check_and_record(data, ts)

    def _enqueue_realtime(self, line: str) -> None:
        line = normalize_monitor_display_line(line)
        if not line:
            return
        self._realtime_pending.append(line)
        self._pending_realtime_chars += len(line)
        if self._pending_realtime_chars > 512_000:
            while self._realtime_pending and self._pending_realtime_chars > 256_000:
                dropped = self._realtime_pending.popleft()
                self._pending_realtime_chars -= len(dropped)
        if not self._log_flush_timer.isActive():
            self._log_flush_timer.start()

    def _enqueue_record(self, line: str, highlight_terms: list[str] | None = None) -> None:
        line = normalize_monitor_display_line(line)
        if not line:
            return
        self._record_pending.append((line, highlight_terms))
        if not self._log_flush_timer.isActive():
            self._log_flush_timer.start()

    _FLUSH_MAX_LINES = 80
    _FLUSH_MAX_LINES_FROZEN = 20
    _COLOR_BLOCK_CHARS = 512_000
    _COLOR_BLOCK_LINES = 20_000

    def _colorization_enabled(self, text_edit: CtrlWheelZoomTextEdit) -> bool:
        if self._pending_realtime_chars > self._COLOR_BLOCK_CHARS:
            return False
        try:
            if text_edit.document().blockCount() > self._COLOR_BLOCK_LINES:
                return False
        except Exception:
            pass
        return True

    def flush_pending_display(self) -> None:
        """Write any batched monitor lines before exporting QTextEdit contents."""
        if self._log_flush_timer.isActive():
            self._log_flush_timer.stop()
        while self._realtime_pending:
            line = self._realtime_pending.popleft()
            self._pending_realtime_chars -= len(line)
            self._append_to_text(self.serial_text, line)
        while self._record_pending:
            line, terms = self._record_pending.popleft()
            self._append_to_text(self.record_text, line, highlight_terms=terms)

    def _flush_log_batch(self) -> None:
        main_window = getattr(self, "_mw", None)
        frozen = (
            main_window is not None and main_window.is_data_display_frozen()
        )
        max_lines = self._FLUSH_MAX_LINES_FROZEN if frozen else self._FLUSH_MAX_LINES
        if frozen and not self._realtime_pending and not self._record_pending:
            if not self._log_flush_timer.isActive():
                self._log_flush_timer.start(50)
            return
        flushed = 0
        while self._realtime_pending and flushed < max_lines:
            line = self._realtime_pending.popleft()
            self._pending_realtime_chars -= len(line)
            self._append_to_text(self.serial_text, line)
            flushed += 1
        while self._record_pending and flushed < max_lines:
            line, terms = self._record_pending.popleft()
            self._append_to_text(self.record_text, line, highlight_terms=terms)
            flushed += 1
        if self._realtime_pending or self._record_pending:
            interval = 50 if frozen else 0
            self._log_flush_timer.start(interval)
        elif flushed:
            self._schedule_main_status_refresh()

    def _schedule_main_status_refresh(self) -> None:
        main_window = getattr(self, "_mw", None)
        if main_window is None:
            return
        scheduler = getattr(main_window, "_schedule_status_refresh", None)
        if callable(scheduler):
            scheduler()

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
            line = format_monitor_raw_line(
                data,
                ts,
                hex_format=bool(self._monitor_is_hex),
            )
            if not line:
                return
            if self._monitor_is_hex:
                highlighted = " ".join(f"{byte:02X}" for byte in pattern)
            else:
                highlighted = self._monitor_pattern_text
            highlight = [highlighted]
            self._enqueue_record(line.rstrip("\n") + "\n", highlight_terms=highlight)

    def _format_data_line(self, data: bytes, ts: float) -> str:
        return format_monitor_raw_line(data, ts, hex_format=bool(self.hex_format))

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
        r"(\[\s*(?:EMERG|ERROR|WARN|NOTICE|INFO|DEBUG|TRACE)\s*\])"
    )

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
        if not terms and not self._colorization_enabled(text_edit):
            cursor = QTextCursor(text_edit.document())
            cursor.movePosition(QTextCursor.MoveOperation.End)
            for segment in build_receive_color_segments(text):
                seg_text, seg_color, seg_bg, _pill = segment
                if not seg_text:
                    continue
                fmt = QTextCharFormat()
                if seg_color:
                    fmt.setForeground(QColor(seg_color))
                if seg_bg:
                    fmt.setBackground(QColor(seg_bg))
                cursor.setCharFormat(fmt)
                cursor.insertText(seg_text)
            if getattr(self, "autoscroll", True):
                scroll_bar = text_edit.verticalScrollBar()
                if scroll_bar is not None and text_edit.document().blockCount() > 500:
                    scroll_bar.setValue(scroll_bar.maximum())
                else:
                    text_edit.setTextCursor(cursor)
                    text_edit.ensureCursorVisible()
            return

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
                fg, bg = LEVEL_STYLES.get(level, ("#374151", "#E8E8E8"))
                _apply(fg, bg)

            cursor.insertText(match.group(0))
            pos = match.end()

        if pos < len(text):
            _insert_plain(text[pos:], "#0000CD", None)

        if getattr(self, "autoscroll", True):
            scroll_bar = text_edit.verticalScrollBar()
            if scroll_bar is not None and text_edit.document().blockCount() > 500:
                scroll_bar.setValue(scroll_bar.maximum())
            else:
                text_edit.setTextCursor(cursor)
                text_edit.ensureCursorVisible()
