"""模拟 MCU 工具页面。

该页面只负责 UI 与上层编排；串口、协议解析、自动回复和产品数据仍由
ProtocolParserApp 及既有业务模块维护。
"""
from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from typing import Callable

from PySide6.QtCore import Qt, QTimer, QEvent, QSize
from PySide6.QtGui import (
    QFont, QFontMetrics, QTextCursor, QTextCharFormat, QColor, QIntValidator,
    QTextDocument, QTextOption, QPalette,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QSplitter,
    QSizePolicy, QAbstractItemView, QHeaderView, QTableWidgetItem,
    QStackedWidget, QFileDialog, QDialog, QMessageBox, QFrame,
    QStyledItemDelegate, QStyleOptionViewItem, QStyle,
)
from qfluentwidgets import (
    CardWidget, BodyLabel, StrongBodyLabel, PushButton, PrimaryPushButton,
    ToggleButton, LineEdit, TextEdit, CheckBox, TableWidget, Pivot, SpinBox,
)

from protocol_parser.ui_helpers import (
    _convert_value, _typeid_name, format_frame_display,
    format_attr_validation_message,
)
from protocol_parser.product_importer import localized_attribute_name
from protocol_parser.exceptions import ProductConfigError
from protocol_parser.combo_font import MatchedPopupComboBox
from protocol_parser.widgets import StyledMessageBox, apply_fluent_dialog_style
from protocol_parser.theme import PALETTE
from protocol_parser.dpi_font import (
    responsive_point_size,
    make_ui_font,
    apply_scoped_font,
    apply_table_font,
    apply_adaptive_geometry,
    fit_text_control,
)


_TEXT_EDIT_QSS = f"""
QTextEdit {{
    color: {PALETTE['text']};
    background-color: {PALETTE['card_bg']};
    border: 1px solid {PALETTE['card_border']};
    border-radius: 6px;
    padding: 6px;
}}
QTextEdit:focus {{ border: 1px solid {PALETTE['primary']}; }}
"""

_RX_TAG_COLOR = "#0078D4"
_TX_TAG_COLOR = "#16833D"
_RX_PARSED_COLOR = "#0066CC"   # RX 解析文字：蓝色
_TX_PARSED_COLOR = "#008800"   # TX 解析文字：绿色


class WrappedAttributeTextDelegate(QStyledItemDelegate):
    """多行文本委托，同时保留 Fluent 表格原有行底色与选中样式。"""

    def __init__(
        self, parent=None, *, base_delegate=None, vertical_padding: int = 24
    ):
        super().__init__(parent)
        self._base_delegate = base_delegate
        self._vertical_padding = max(12, int(vertical_padding))

    def _text_height(self, option, index) -> int:
        view = self.parent()
        width = 0
        if view is not None and hasattr(view, "columnWidth"):
            try:
                width = int(view.columnWidth(index.column()))
            except Exception:
                width = 0
        if width <= 1:
            width = int(option.rect.width())
        width = max(24, width - 36)

        document = QTextDocument()
        document.setDefaultFont(option.font)
        document.setDocumentMargin(0.0)
        text_option = document.defaultTextOption()
        text_option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        document.setDefaultTextOption(text_option)
        document.setPlainText(str(index.data(Qt.ItemDataRole.DisplayRole) or ""))
        document.setTextWidth(width)
        document.adjustSize()
        height = max(
            float(document.size().height()),
            float(document.documentLayout().documentSize().height()),
        )
        return int(height + 0.999)

    def sizeHint(self, option, index):  # type: ignore[override]
        delegate = self._base_delegate
        if delegate is not None:
            base = delegate.sizeHint(option, index)
        else:
            base = super().sizeHint(option, index)
        height = max(base.height(), self._text_height(option, index) + self._vertical_padding)
        return QSize(base.width(), height)

    def paint(self, painter, option, index):  # type: ignore[override]
        wrapped = QStyleOptionViewItem(option)
        self.initStyleOption(wrapped, index)
        wrapped.features |= QStyleOptionViewItem.ViewItemFeature.WrapText
        wrapped.textElideMode = Qt.TextElideMode.ElideNone
        # Fluent 的浅色选中背景必须配深色文字；标准委托默认会把
        # HighlightedText 设成白色，导致名称/属性文本在浅色选中行中消失。
        if wrapped.state & QStyle.StateFlag.State_Selected:
            text_color = QColor(PALETTE["text"])
            wrapped.palette.setColor(QPalette.ColorRole.Text, text_color)
            wrapped.palette.setColor(QPalette.ColorRole.HighlightedText, text_color)
        delegate = self._base_delegate
        if delegate is not None:
            delegate.paint(painter, wrapped, index)
        else:
            super().paint(painter, wrapped, index)


class CtrlWheelZoomTextEdit(TextEdit):
    """实时数据专用文本框。

    字号通过 :meth:`set_data_font_point_size` 明确设置，只影响当前数据框，
    不改变应用程序、按钮、下拉框或其他页面的字体。保留 Ctrl+滚轮和
    隐藏的字号状态控件，界面不再占用空间显示字号调节器。
    """

    _FONT_MIN_PT = 8
    _FONT_MAX_PT = 24

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._data_font_point_size = 10
        self.viewport().installEventFilter(self)
        self.setToolTip("按住 Ctrl 并滚动鼠标滚轮可调整本数据框字体大小")

    def data_font_point_size(self) -> int:
        """返回当前数据框字号（pt）。"""
        return int(self._data_font_point_size)

    def set_data_font_point_size(self, point_size: int) -> None:
        """设置当前数据框的字体大小，并立即作用于已有和后续文本。"""
        size = max(self._FONT_MIN_PT, min(self._FONT_MAX_PT, int(point_size)))
        self._data_font_point_size = size

        widget_font = QFont(self.font())
        widget_font.setPointSize(size)
        self.setFont(widget_font)

        document_font = QFont(self.document().defaultFont())
        if document_font.family() == "":
            document_font.setFamily(widget_font.family())
        document_font.setPointSize(size)
        self.document().setDefaultFont(document_font)

        # 保持光标后续插入文本也使用相同字号；颜色、粗体等格式不受影响。
        current_format = self.currentCharFormat()
        current_format.setFontPointSize(float(size))
        self.setCurrentCharFormat(current_format)
        self.viewport().update()

    @staticmethod
    def _wheel_delta(event) -> int:
        """兼容普通滚轮和高精度触控板。"""
        delta = int(event.angleDelta().y())
        if delta == 0:
            delta = int(event.pixelDelta().y())
        return delta

    def _handle_ctrl_wheel(self, event) -> bool:
        modifiers = event.modifiers()
        if not (modifiers & Qt.KeyboardModifier.ControlModifier):
            return False

        delta = self._wheel_delta(event)
        if delta == 0:
            event.accept()
            return True

        # 普通鼠标每格通常为 120；触控板可能返回更小或更大的增量。
        notch_count = max(1, abs(delta) // 120)
        direction = 1 if delta > 0 else -1
        self.set_data_font_point_size(
            self._data_font_point_size + direction * notch_count
        )
        event.accept()
        return True

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if watched is self.viewport() and event.type() == QEvent.Type.Wheel:
            if self._handle_ctrl_wheel(event):
                return True
        return super().eventFilter(watched, event)

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        # 保留直接发送给 QTextEdit 本体时的兼容路径。
        if self._handle_ctrl_wheel(event):
            return
        super().wheelEvent(event)


class McuSimulatePage(QWidget):
    """页签2：模拟 MCU 自动回复、实时属性和预置命令。"""

    def __init__(self, main_window: QWidget):
        super().__init__(main_window)
        self.setObjectName("mcuSimulateToolPage")
        self._mw = main_window
        self._auto_scroll = True
        self._product_syncing = False
        self._attr_row_by_id: dict[int, int] = {}
        self._attr_send_edits: dict[int, LineEdit] = {}
        self._attr_select_checks: dict[int, CheckBox] = {}
        self._syncing_attr_selection = False
        self._poweron_builders: list[tuple[str, Callable[[], bytes]]] = []
        # 记录用户手动拖动调整过的侧栏宽度，切换单个面板显隐时不重置另一个面板
        self._panel_width_mem: dict[str, int] = {}
        self._pending_data_segments: deque[tuple[str, str, bool]] = deque()
        self._pending_data_chars = 0
        self._data_flush_timer = QTimer(self)
        self._data_flush_timer.setSingleShot(True)
        self._data_flush_timer.setInterval(40)
        self._data_flush_timer.timeout.connect(self._flush_data_batch)
        self._applying_dpi_metrics = False
        self._first_show_relayout_done = False
        self._layout_resize_timer = QTimer(self)
        self._layout_resize_timer.setSingleShot(True)
        self._layout_resize_timer.setInterval(70)
        self._layout_resize_timer.timeout.connect(self._apply_debounced_mcu_layout)
        self._side_font_point_size = 10
        self._attr_base_row_height = 38
        self._enum_button_height = 28
        self._preset_row_height = 34
        # BuildFix22: 实时属性列宽完全由内容测量，当前值高频更新时用
        # 250 ms 防抖合并，避免表格持续跳动。
        self._attr_column_minimums = {
            0: 48, 1: 88, 2: 150, 3: 190,
            4: 74, 5: 88, 6: 100, 7: 190,
        }
        # 名称与属性文本采用“内容测量 + 可用宽度上限 + 完整换行”。
        # 宽面板时适度加宽以减少无意义高行，窄面板时保持紧凑并由行高
        # 承载全部文字；绝不再用省略号隐藏内容。
        self._attr_wrapped_column_maximums = {2: 260, 3: 320}
        self._attr_wrapped_column_ratios = {2: 0.26, 3: 0.30}
        self._pending_attr_remeasure_columns: set[int] = set()
        self._attr_column_remeasure_timer = QTimer(self)
        self._attr_column_remeasure_timer.setSingleShot(True)
        self._attr_column_remeasure_timer.setInterval(250)
        self._attr_column_remeasure_timer.timeout.connect(
            self._apply_scheduled_attr_column_remeasure
        )
        # 表格 cellWidget 和字体布局通常要到本轮事件循环结束后 sizeHint 才稳定。
        # 使用独立防抖定时器在最终列宽确定后重算行高，避免换行文字被裁掉。
        self._attr_row_resize_timer = QTimer(self)
        self._attr_row_resize_timer.setSingleShot(True)
        self._attr_row_resize_timer.setInterval(80)
        self._attr_row_resize_timer.timeout.connect(
            self._resize_attr_rows_to_wrapped_content
        )
        self._build_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 2, 0, 0)
        root.setSpacing(6)

        operation = CardWidget(self)
        self.operation_card = operation
        self.operation_layout = QGridLayout(operation)
        self.operation_layout.setContentsMargins(12, 8, 12, 8)
        self.operation_layout.setHorizontalSpacing(8)
        self.operation_layout.setVerticalSpacing(6)
        self.product_label = BodyLabel("当前产品：", operation)
        self.product_combo = MatchedPopupComboBox(operation)
        self.product_combo.setMinimumWidth(220)
        self.product_combo.setMaximumWidth(16_777_215)
        self.product_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.product_combo.currentTextChanged.connect(self._on_product_changed)
        self.import_json_button = PushButton("导入产品JSON", operation)
        self.import_json_button.clicked.connect(self._import_product_json)
        self.manage_json_button = PushButton("修改/删除产品", operation)
        self.manage_json_button.clicked.connect(self._edit_or_delete_product)
        self.manage_json_button.setEnabled(False)

        self.attr_visible_toggle = ToggleButton("实时属性", operation)
        self.attr_visible_toggle.setChecked(False)
        self.attr_visible_toggle.toggled.connect(self._set_attr_panel_visible)

        self.preset_visible_toggle = ToggleButton("预置命令", operation)
        self.preset_visible_toggle.setChecked(False)
        self.preset_visible_toggle.toggled.connect(self._set_preset_panel_visible)
        for button in (
            self.import_json_button,
            self.manage_json_button,
            self.attr_visible_toggle,
            self.preset_visible_toggle,
        ):
            button.setSizePolicy(
                QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
            )
            fit_text_control(button)
        self._operation_widgets = (
            self.product_label,
            self.product_combo,
            self.import_json_button,
            self.manage_json_button,
            self.attr_visible_toggle,
            self.preset_visible_toggle,
        )
        self._relayout_operation_bar()
        root.addWidget(operation)

        # 主内容区改为横向三栏：实时数据 | 实时属性 | 预置命令。
        # 实时属性和预置命令不再占用数据窗口下方空间，而是位于其右侧。
        self.content_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.content_splitter.setObjectName("mcuDataAttributePresetSplitter")
        self.content_splitter.setHandleWidth(6)
        self.content_splitter.setChildrenCollapsible(False)

        self.data_card = CardWidget(self.content_splitter)
        data_layout = QVBoxLayout(self.data_card)
        data_layout.setContentsMargins(12, 8, 12, 8)
        data_layout.setSpacing(6)
        self.data_bar = QGridLayout()
        self.data_bar.setContentsMargins(0, 0, 0, 0)
        self.data_bar.setHorizontalSpacing(8)
        self.data_bar.setVerticalSpacing(6)
        self.data_title_label = StrongBodyLabel("实时数据（协议解析）", self.data_card)
        self.data_font_label = BodyLabel("字号：", self.data_card)
        self.data_font_spin = SpinBox(self.data_card)
        self.data_font_spin.setRange(
            CtrlWheelZoomTextEdit._FONT_MIN_PT,
            CtrlWheelZoomTextEdit._FONT_MAX_PT,
        )
        initial_data_font_size = self._mw.font().pointSize()
        if initial_data_font_size <= 0:
            initial_data_font_size = 10
        self.data_font_spin.setValue(initial_data_font_size)
        self.data_font_spin.setSuffix(" pt")
        # qfluentwidgets 的 SpinBox 左右步进按钮会占用较多宽度。
        # 旧值 92 在高 DPI/不同分辨率下会把数值编辑区挤到几乎不可见，
        # 只剩上下箭头。这里为“24 pt”预留稳定的文本区域。
        self.data_font_spin.setMinimumWidth(132)
        self.data_font_spin.setMaximumWidth(148)
        self.data_font_spin.lineEdit().setMinimumWidth(48)
        self.data_font_spin.lineEdit().setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self.data_font_spin.setToolTip("仅调整模拟 MCU 实时数据框中的字体大小")
        # 保留完整字号逻辑、取值范围和 valueChanged 连接，但不再在界面上
        # 展示标签与 SpinBox，避免工具栏被字号控件占用。
        self.data_font_label.hide()
        self.data_font_spin.hide()
        self.clear_button = PushButton("清空", self.data_card)
        self.clear_button.clicked.connect(self._clear_data)
        self.autoscroll_button = ToggleButton("自动滚动", self.data_card)
        self.autoscroll_button.setChecked(True)
        self.autoscroll_button.toggled.connect(
            lambda checked: setattr(self, "_auto_scroll", bool(checked))
        )
        self._data_bar_widgets = (
            self.data_title_label, self.clear_button, self.autoscroll_button,
        )
        self._relayout_data_bar()
        data_layout.addLayout(self.data_bar)
        self.data_text = CtrlWheelZoomTextEdit(self.data_card)
        self.data_text.setObjectName("McuRealtimeDataText")
        self.data_text.setProperty("smstIndependentDataFont", True)
        self.data_text.setStyleSheet(_TEXT_EDIT_QSS)
        self.data_text.setReadOnly(True)
        self.data_text.setAcceptRichText(False)
        self.data_text.setUndoRedoEnabled(False)
        max_lines = max(100, int(getattr(self._mw, "max_display_lines", 10000)))
        self.data_text.document().setMaximumBlockCount(max_lines)
        self.data_text.setFont(QFont(self._mw.font()))
        self.data_text.set_data_font_point_size(self.data_font_spin.value())
        self.data_font_spin.valueChanged.connect(
            self.data_text.set_data_font_point_size
        )
        data_layout.addWidget(self.data_text, 1)

        self.attr_card = self._build_attr_card()
        self.preset_card = self._build_preset_card()
        self.data_card.setMinimumWidth(320)
        self.attr_card.setMinimumWidth(260)
        self.preset_card.setMinimumWidth(260)
        # In the narrow-screen vertical arrangement each panel keeps enough
        # height for its toolbar/header; the table/text body then scrolls.
        self.data_card.setMinimumHeight(120)
        self.attr_card.setMinimumHeight(96)
        self.preset_card.setMinimumHeight(96)

        self.content_splitter.addWidget(self.data_card)
        self.content_splitter.addWidget(self.attr_card)
        self.content_splitter.addWidget(self.preset_card)
        # 只让数据窗口吸收窗口缩放，侧栏保持各自宽度，避免调好的尺寸被重置。
        self.content_splitter.setStretchFactor(0, 1)
        self.content_splitter.setStretchFactor(1, 0)
        self.content_splitter.setStretchFactor(2, 0)
        self.content_splitter.setSizes([760, 380, 380])
        # 记录用户手动拖动调整过的侧栏宽度。
        self.content_splitter.splitterMoved.connect(self._on_splitter_moved)

        # 保留旧属性名，避免其他已有代码引用 lower_splitter 时失效。
        self.lower_splitter = self.content_splitter
        root.addWidget(self.content_splitter, 1)

        # 同步按钮初始开关状态到对应面板（setChecked 不会触发 toggled 信号）
        self._set_attr_panel_visible(self.attr_visible_toggle.isChecked())
        self._set_preset_panel_visible(self.preset_visible_toggle.isChecked())
        self.apply_dpi_metrics()

    def apply_dpi_metrics(self, point_size: int | None = None) -> None:
        """Keep MCU side-panel text readable on every logical resolution/DPI.

        The real-time data QTextEdit is intentionally excluded because its
        hidden字号状态控件与 Ctrl+滚轮逻辑独立维护该字体。
        """
        if self._applying_dpi_metrics:
            return
        self._applying_dpi_metrics = True
        try:
            # Qt point fonts already follow the current monitor DPI.  Keep one
            # stable UI point size and adapt widget geometry instead of
            # multiplying text again from the 2K/4K pixel count.
            resolved = int(point_size or responsive_point_size(self, maximum=14))
            resolved = max(8, min(14, resolved))
            font_changed = resolved != self._side_font_point_size
            self._side_font_point_size = resolved
            font = make_ui_font(resolved)
            metrics = QFontMetrics(font)

            apply_scoped_font(self, resolved)
            self.product_combo.setFont(font)

            self._attr_base_row_height = max(38, metrics.height() + 16)
            self._enum_button_height = max(28, metrics.height() + 10)
            self._preset_row_height = max(34, metrics.height() + 14)

            for table in (self.attr_table, self.poweron_table, self.autoreply_table):
                apply_table_font(table, font, minimum_padding=16)

            header = self.attr_table.horizontalHeader()
            header.setMinimumHeight(max(32, metrics.height() + 12))

            # Existing rows and cell widgets were created before a monitor
            # moved to another screen; update them in place without changing
            # product/serial/auto-reply logic.
            for row in range(self.poweron_table.rowCount()):
                self.poweron_table.setRowHeight(row, self._preset_row_height)
            for row in range(self.autoreply_table.rowCount()):
                self.autoreply_table.setRowHeight(row, self._preset_row_height)
            if font_changed and self.attr_table.rowCount() > 0:
                # Rebuild only on an actual DPI point-size transition so enum
                # rows can both grow and shrink according to their button grid.
                self.refresh_attr_table()
            else:
                for row in range(self.attr_table.rowCount()):
                    self.attr_table.setRowHeight(
                        row, max(self.attr_table.rowHeight(row), self._attr_base_row_height)
                    )

            # Recompute all button/input/tab geometry from font metrics.  This
            # prevents qfluentwidgets controls created with a smaller sizeHint
            # from keeping a stale fixed height after a monitor/DPI change.
            apply_adaptive_geometry(self, resolved)
            self._relayout_operation_bar()
            self._relayout_data_bar()
            self._relayout_attr_header()
            self._relayout_autoreply_header()
            self._layout_common_commands()
            self._remeasure_attr_columns()
            self._resize_attr_rows_to_wrapped_content()
            self._schedule_attr_row_resize(0)
            QTimer.singleShot(160, self._resize_attr_rows_to_wrapped_content)
            self._apply_preset_table_widths()
            self.attr_card.setMinimumWidth(min(self._attr_ideal_width(), 320))
            self.preset_card.setMinimumWidth(min(self._preset_ideal_width(), 320))

            # Preserve the user's independent data-window setting after the
            # application font is refreshed.
            self.data_text.set_data_font_point_size(self.data_font_spin.value())
            self._schedule_lower_panel_rebalance()
        finally:
            self._applying_dpi_metrics = False

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.ApplicationFontChange,
            QEvent.Type.FontChange,
            QEvent.Type.ScreenChangeInternal,
        ):
            QTimer.singleShot(0, self.apply_dpi_metrics)

    def _relayout_operation_bar(self) -> None:
        """Wrap the product/action bar according to available logical width."""
        layout = getattr(self, "operation_layout", None)
        widgets = getattr(self, "_operation_widgets", ())
        if layout is None or not widgets:
            return
        for widget in widgets:
            layout.removeWidget(widget)
        self._reset_grid_stretches(layout)

        width = max(1, int(self.width()))
        for widget in widgets:
            try:
                fit_text_control(widget, point_size=self._side_font_point_size)
            except Exception:
                pass
        wide_needed = sum(
            max(widget.minimumWidth(), widget.sizeHint().width())
            for widget in widgets
        ) + 56
        medium_needed = max(660, self.product_combo.minimumWidth() + 320)
        if width >= wide_needed:
            layout.addWidget(self.product_label, 0, 0)
            layout.addWidget(self.product_combo, 0, 1)
            layout.addWidget(self.import_json_button, 0, 2)
            layout.addWidget(self.manage_json_button, 0, 3)
            layout.addWidget(self.attr_visible_toggle, 0, 4)
            layout.addWidget(self.preset_visible_toggle, 0, 5)
            layout.setColumnStretch(1, 1)
        elif width >= medium_needed:
            layout.addWidget(self.product_label, 0, 0)
            layout.addWidget(self.product_combo, 0, 1, 1, 4)
            layout.addWidget(self.import_json_button, 1, 1)
            layout.addWidget(self.manage_json_button, 1, 2)
            layout.addWidget(self.attr_visible_toggle, 1, 3)
            layout.addWidget(self.preset_visible_toggle, 1, 4)
            layout.setColumnStretch(1, 1)
        else:
            layout.addWidget(self.product_label, 0, 0)
            layout.addWidget(self.product_combo, 0, 1, 1, 3)
            layout.addWidget(self.import_json_button, 1, 0)
            layout.addWidget(self.manage_json_button, 1, 1)
            layout.addWidget(self.attr_visible_toggle, 1, 2)
            layout.addWidget(self.preset_visible_toggle, 1, 3)
            layout.setColumnStretch(1, 1)
        for button in (
            self.import_json_button,
            self.manage_json_button,
            self.attr_visible_toggle,
            self.preset_visible_toggle,
        ):
            layout.setAlignment(
                button,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
        layout.invalidate()
        if getattr(self, "operation_card", None) is not None:
            self.operation_card.updateGeometry()

    @staticmethod
    def _remove_grid_widgets(layout, widgets) -> None:
        if layout is None:
            return
        for widget in widgets:
            try:
                layout.removeWidget(widget)
            except Exception:
                pass

    @staticmethod
    def _reset_grid_stretches(layout, count: int = 12) -> None:
        """清除响应式重排前一状态遗留的行列拉伸系数。"""
        if layout is None:
            return
        for index in range(max(1, int(count))):
            layout.setColumnStretch(index, 0)
            layout.setRowStretch(index, 0)

    def _relayout_data_bar(self) -> None:
        layout = getattr(self, "data_bar", None)
        widgets = getattr(self, "_data_bar_widgets", ())
        if layout is None or not widgets:
            return
        self._remove_grid_widgets(layout, widgets)
        self._reset_grid_stretches(layout)
        card = getattr(self, "data_card", None)
        width = int(card.contentsRect().width()) if card is not None else 0
        if width <= 1:
            width = int(card.width()) if card is not None else 0
        if width <= 1:
            width = max(1, int(self.width()) - 48)
        if width >= 520:
            positions = (
                (self.data_title_label, 0, 0, 1, 1),
                (self.clear_button, 0, 1, 1, 1),
                (self.autoscroll_button, 0, 2, 1, 1),
            )
            layout.setColumnStretch(0, 1)
        else:
            positions = (
                (self.data_title_label, 0, 0, 1, 2),
                (self.clear_button, 1, 0, 1, 1),
                (self.autoscroll_button, 1, 1, 1, 1),
            )
            layout.setColumnStretch(0, 1)
            layout.setColumnStretch(1, 1)
        for widget, row, column, row_span, column_span in positions:
            layout.addWidget(widget, row, column, row_span, column_span)
        layout.invalidate()

    def _relayout_attr_header(self) -> None:
        layout = getattr(self, "attr_header_layout", None)
        widgets = getattr(self, "_attr_header_widgets", ())
        if layout is None or not widgets:
            return
        self._remove_grid_widgets(layout, widgets)
        self._reset_grid_stretches(layout)
        card = getattr(self, "attr_card", None)
        width = int(card.contentsRect().width()) if card is not None else 0
        if width <= 1:
            width = int(card.width()) if card is not None else 0
        if width <= 1:
            width = self._attr_ideal_width()
        if width >= 460:
            positions = (
                (self.attr_title_label, 0, 0, 1, 1),
                (self.attr_select_all_check, 0, 1, 1, 1),
                (self.batch_report_button, 0, 2, 1, 1),
            )
            layout.setColumnStretch(0, 1)
        else:
            positions = (
                (self.attr_title_label, 0, 0, 1, 2),
                (self.attr_select_all_check, 1, 0, 1, 1),
                (self.batch_report_button, 1, 1, 1, 1),
            )
            layout.setColumnStretch(0, 1)
            layout.setColumnStretch(1, 1)
        for widget, row, column, row_span, column_span in positions:
            layout.addWidget(widget, row, column, row_span, column_span)
        layout.invalidate()

    def _relayout_autoreply_header(self) -> None:
        layout = getattr(self, "autoreply_header_layout", None)
        widgets = getattr(self, "_autoreply_header_widgets", ())
        if layout is None or not widgets:
            return
        self._remove_grid_widgets(layout, widgets)
        self._reset_grid_stretches(layout)
        page = getattr(self, "autoreply_page", None)
        width = int(page.contentsRect().width()) if page is not None else 0
        if width <= 1:
            width = int(page.width()) if page is not None else 0
        if width <= 1:
            width = self._preset_ideal_width()
        if width >= 440:
            layout.addWidget(self.auto_reply_switch, 0, 0)
            layout.addWidget(self.autoreply_description, 0, 1)
            layout.setColumnStretch(1, 1)
        else:
            layout.addWidget(self.auto_reply_switch, 0, 0)
            layout.addWidget(self.autoreply_description, 1, 0)
            layout.setColumnStretch(0, 1)
        layout.invalidate()

    def _update_content_orientation(self) -> None:
        splitter = getattr(self, "content_splitter", None)
        if splitter is None:
            return
        attr_on = getattr(self, "attr_card", None) is not None and not self.attr_card.isHidden()
        preset_on = getattr(self, "preset_card", None) is not None and not self.preset_card.isHidden()
        # Side-by-side cards need more logical width than is available on many
        # 1366×768 / 125–150% desktops.  Stack them vertically rather than
        # squeezing table headers and button captions to half-height/width.
        use_vertical = bool(attr_on or preset_on) and int(self.width()) < 1120
        orientation = (
            Qt.Orientation.Vertical if use_vertical else Qt.Orientation.Horizontal
        )
        if splitter.orientation() != orientation:
            splitter.setOrientation(orientation)
            self._content_orientation_name = "vertical" if use_vertical else "horizontal"
            splitter.updateGeometry()

    def _build_attr_card(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)
        self.attr_header_layout = QGridLayout()
        self.attr_header_layout.setContentsMargins(0, 0, 0, 0)
        self.attr_header_layout.setHorizontalSpacing(8)
        self.attr_header_layout.setVerticalSpacing(6)
        self.attr_title_label = StrongBodyLabel("实时属性", card)
        self.attr_select_all_check = CheckBox("全选", card)
        # 全选框只允许“选中/未选中”两态。三态复选框从未选中点击时
        # 会先进入 PartiallyChecked，旧逻辑因此把第一次点击误判为取消全选。
        self.attr_select_all_check.setTristate(False)
        self.attr_select_all_check.toggled.connect(self._on_select_all_toggled)
        self.batch_report_button = PushButton("MCU-批量上报", card)
        self.batch_report_button.clicked.connect(self._on_batch_report)
        self._attr_header_widgets = (
            self.attr_title_label, self.attr_select_all_check, self.batch_report_button
        )
        self._relayout_attr_header()
        layout.addLayout(self.attr_header_layout)

        self.attr_table = TableWidget(card)
        self.attr_table.setColumnCount(8)
        self.attr_table.setHorizontalHeaderLabels([
            "选", "ID", "名称", "属性文本", "权限", "格式", "当前值", "发送",
        ])
        self.attr_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.attr_table.setAlternatingRowColors(True)
        self.attr_table.setObjectName("AttributeTable")
        table_palette = self.attr_table.palette()
        table_palette.setColor(
            QPalette.ColorRole.HighlightedText, QColor(PALETTE["text"])
        )
        self.attr_table.setPalette(table_palette)
        self.attr_table.setStyleSheet(
            self.attr_table.styleSheet()
            + f"""
QTableView#AttributeTable::item:selected {{
    color: {PALETTE['text']};
}}
"""
        )
        self.attr_table.verticalHeader().setVisible(False)
        self.attr_table.verticalHeader().setDefaultSectionSize(38)
        header_view = self.attr_table.horizontalHeader()
        # 8 列全部锁定为 Fixed。列宽只由内容测量函数维护，用户不能
        # 手动拖窄，面板放不下时由表格内部横向滚动条兜底。
        for column in range(self.attr_table.columnCount()):
            header_view.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        header_view.setSectionsMovable(False)
        header_view.setCascadingSectionResizes(False)
        header_view.setStretchLastSection(False)
        header_view.setMinimumSectionSize(46)
        self.attr_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.attr_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        # 名称列采用受控窄列 + 自动换行。关闭省略号，完整内容由行高
        # 自动增长承载；其余未受限列仍按内容测量宽度。
        self.attr_table.setWordWrap(True)
        self.attr_table.setTextElideMode(Qt.TextElideMode.ElideNone)
        self.attr_table.setProperty("smstPreserveWrappedRowHeight", True)
        self._attr_base_delegate = self.attr_table.itemDelegate()
        self._attr_wrap_delegate = WrappedAttributeTextDelegate(
            self.attr_table, base_delegate=self._attr_base_delegate
        )
        self.attr_table.setItemDelegateForColumn(2, self._attr_wrap_delegate)
        self.attr_table.setItemDelegateForColumn(3, self._attr_wrap_delegate)
        for column, minimum in self._attr_column_minimums.items():
            self.attr_table.setColumnWidth(column, minimum)
        layout.addWidget(self.attr_table, 1)
        return card

    def _on_select_all_toggled(self, checked: bool) -> None:
        """全选/取消全选实时属性，不发送任何数据。"""
        if self._syncing_attr_selection:
            return
        self._syncing_attr_selection = True
        try:
            for check in self._attr_select_checks.values():
                if not check.isEnabled():
                    continue
                check.blockSignals(True)
                check.setChecked(bool(checked))
                check.blockSignals(False)
        finally:
            self._syncing_attr_selection = False
        self._sync_select_all_state()

    def _on_row_select_changed(self, state: int) -> None:
        del state
        if not self._syncing_attr_selection:
            self._sync_select_all_state()

    def _sync_select_all_state(self) -> None:
        selector = getattr(self, "attr_select_all_check", None)
        if selector is None:
            return
        checks = [check for check in self._attr_select_checks.values() if check.isEnabled()]
        checked_count = sum(1 for check in checks if check.isChecked())
        # 全选框保持两态：只有所有行都选中时显示选中；部分选中时显示未选中。
        # 这样用户在“部分选中”状态点击一次即可真正全选。
        resolved = bool(checks) and checked_count == len(checks)
        selector.blockSignals(True)
        try:
            selector.setChecked(resolved)
        finally:
            selector.blockSignals(False)

    def _build_preset_card(self) -> CardWidget:
        card = CardWidget(self)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)
        title_row = QHBoxLayout()
        title_row.addWidget(StrongBodyLabel("预置命令", card))
        title_row.addStretch(1)
        layout.addLayout(title_row)

        self.preset_pivot = Pivot(card)
        self.preset_stack = QStackedWidget(card)
        self.poweron_page = self._build_poweron_page()
        self.autoreply_page = self._build_autoreply_page()
        self.common_page = self._build_common_page()
        self.preset_stack.addWidget(self.poweron_page)
        self.preset_stack.addWidget(self.autoreply_page)
        self.preset_stack.addWidget(self.common_page)

        pages = (
            ("poweron", "上电流程", 0),
            ("autoreply", "日常自动回复", 1),
            ("common", "常用命令", 2),
        )
        for key, text, index in pages:
            self.preset_pivot.addItem(
                routeKey=key,
                text=text,
                onClick=lambda checked=False, idx=index: self._select_preset_index(idx),
            )
        self.preset_pivot.setCurrentItem("poweron")
        layout.addWidget(self.preset_pivot)
        layout.addWidget(self.preset_stack, 1)
        return card

    def _build_poweron_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.poweron_table = TableWidget(page)
        self.poweron_table.setColumnCount(3)
        self.poweron_table.setHorizontalHeaderLabels(["序号", "命令", "操作"])
        header = self.poweron_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.poweron_table.setColumnWidth(0, 58)
        self.poweron_table.setColumnWidth(2, 92)
        self.poweron_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.poweron_table.setWordWrap(False)
        self.poweron_table.verticalHeader().setVisible(False)
        layout.addWidget(self.poweron_table, 1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.btn_poweron_send_all = PrimaryPushButton("一键发送全部", page)
        self.btn_poweron_send_all.clicked.connect(self._on_poweron_send_all)
        buttons.addWidget(self.btn_poweron_send_all)
        layout.addLayout(buttons)
        return page

    def _build_autoreply_page(self) -> QWidget:
        page = QWidget(self)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        self.autoreply_header_layout = QGridLayout()
        self.autoreply_header_layout.setContentsMargins(0, 0, 0, 0)
        self.autoreply_header_layout.setHorizontalSpacing(8)
        self.autoreply_header_layout.setVerticalSpacing(4)
        self.auto_reply_switch = ToggleButton("启用自动回复", page)
        self.auto_reply_switch.toggled.connect(self._on_auto_reply_toggled)
        self.autoreply_description = BodyLabel("监听目标命令后自动回复", page)
        self.autoreply_description.setEnabled(False)
        self.autoreply_description.setWordWrap(True)
        self._autoreply_header_widgets = (
            self.auto_reply_switch, self.autoreply_description
        )
        self._relayout_autoreply_header()
        layout.addLayout(self.autoreply_header_layout)
        self.autoreply_table = TableWidget(page)
        self.autoreply_table.setColumnCount(3)
        self.autoreply_table.setHorizontalHeaderLabels(["启用", "名称", "说明"])
        header = self.autoreply_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.autoreply_table.setColumnWidth(0, 58)
        self.autoreply_table.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.autoreply_table.setWordWrap(False)
        self.autoreply_table.verticalHeader().setVisible(False)
        layout.addWidget(self.autoreply_table, 1)
        return page

    def _build_common_page(self) -> QWidget:
        page = QWidget(self)
        self.common_layout = QGridLayout(page)
        self.common_layout.setContentsMargins(6, 6, 6, 6)
        self.common_layout.setSpacing(8)
        return page

    def _set_attr_panel_visible(self, visible: bool) -> None:
        """显示或隐藏实时属性面板。

        使用控件自身的显隐状态控制右侧栏，随后重新分配三栏宽度。
        这样关闭后再次点击按钮，可以可靠恢复对应面板。
        """
        card = getattr(self, "attr_card", None)
        if card is not None:
            card.setVisible(bool(visible))
        self._schedule_lower_panel_rebalance()

    def _set_preset_panel_visible(self, visible: bool) -> None:
        """显示或隐藏预置命令面板。"""
        card = getattr(self, "preset_card", None)
        if card is not None:
            card.setVisible(bool(visible))
        self._schedule_lower_panel_rebalance()

    def _schedule_lower_panel_rebalance(self) -> None:
        """等待 Qt 完成本轮可见性更新后再重新分配 splitter 空间。"""
        QTimer.singleShot(0, self._rebalance_lower_panels)

    def _on_splitter_moved(self, pos: int, index: int) -> None:
        """记录用户手动拖动调整后的侧栏宽度，供后续重分配时保留。"""
        if getattr(self, "_rebalancing", False):
            return
        splitter = getattr(self, "content_splitter", None)
        attr_card = getattr(self, "attr_card", None)
        preset_card = getattr(self, "preset_card", None)
        if splitter is None or attr_card is None or preset_card is None:
            return
        sizes = splitter.sizes()
        if len(sizes) < 3:
            return
        if not attr_card.isHidden() and sizes[1] > 0:
            self._panel_width_mem["attr"] = sizes[1]
        if not preset_card.isHidden() and sizes[2] > 0:
            self._panel_width_mem["preset"] = sizes[2]

    def _measure_attr_column_width(self, column: int) -> int:
        """按表头、文本和单元格控件的实际尺寸计算属性列宽。"""
        table = self.attr_table
        if column < 0 or column >= table.columnCount():
            return 0
        font = make_ui_font(self._side_font_point_size)
        metrics = QFontMetrics(font)
        header_item = table.horizontalHeaderItem(column)
        header_text = header_item.text() if header_item is not None else ""
        width = metrics.horizontalAdvance(str(header_text or ""))
        contains_widget = False

        for row in range(table.rowCount()):
            item = table.item(row, column)
            if item is not None:
                width = max(width, metrics.horizontalAdvance(str(item.text() or "")))
            cell = table.cellWidget(row, column)
            if cell is not None:
                contains_widget = True
                layout = cell.layout()
                if layout is not None:
                    layout.activate()
                width = max(
                    width,
                    int(cell.sizeHint().width()),
                    int(cell.minimumSizeHint().width()),
                )

        padding = 16 if contains_widget else 28
        minimum = int(self._attr_column_minimums.get(column, 46))
        measured = max(minimum, width + padding)
        maximum = self._attr_wrapped_column_maximums.get(column)
        if maximum is not None:
            # 受控换行列根据当前表格可用宽度动态变化。宽屏减少换行，
            # 窄屏保持紧凑；实际内容仍由自动行高完整显示。
            viewport_width = int(table.viewport().width())
            ratio = float(self._attr_wrapped_column_ratios.get(column, 0.26))
            adaptive_cap = int(maximum)
            if viewport_width > 1:
                adaptive_cap = min(adaptive_cap, max(minimum, int(viewport_width * ratio)))
            return max(minimum, min(adaptive_cap, measured))
        return measured

    def _wrapped_attr_text_height(self, text: object, width: int) -> int:
        """返回按当前表格字体和列宽完整换行后的文本高度。

        ``QFontMetrics.boundingRect`` 在中英文、连字符和长连续文本混排时，
        与 QTableView 委托的实际换行结果可能存在一行偏差。使用
        ``QTextDocument`` 并启用 ``WrapAtWordBoundaryOrAnywhere``，可确保
        类似 ``属性0x02-属性0x02-...`` 的连续内容也能完整断行。
        """
        document = QTextDocument()
        document.setDefaultFont(self.attr_table.font())
        document.setDocumentMargin(0.0)
        option = document.defaultTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        document.setDefaultTextOption(option)
        document.setPlainText(str(text or ""))
        document.setTextWidth(max(24, int(width)))
        document.adjustSize()
        layout_height = float(document.documentLayout().documentSize().height())
        size_height = float(document.size().height())
        return max(0, int(max(layout_height, size_height) + 0.999))

    def _resize_attr_rows_to_wrapped_content(self, rows=None) -> None:
        """根据最终列宽、完整换行文本和 cellWidget 自动调整行高。

        先让 Qt 的委托执行一次 ``resizeRowsToContents``，再按受控换行列
        使用 QTextDocument 精确测量，并把枚举按钮组、输入框和“上报”
        按钮的 sizeHint 纳入最终高度。这样宽度变化、DPI 变化和产品切换后
        都不会出现最后一行文字被裁掉的情况。
        """
        table = getattr(self, "attr_table", None)
        if table is None:
            return
        if rows is None:
            selected_rows = list(range(table.rowCount()))
        else:
            selected_rows = sorted({int(value) for value in rows})

        if not selected_rows:
            return

        # 先使用 Qt 委托的内容高度作为第一层基准。
        table.resizeRowsToContents()
        wrapped_columns = tuple(self._attr_wrapped_column_maximums)
        table.setUpdatesEnabled(False)
        try:
            for row in selected_rows:
                if row < 0 or row >= table.rowCount():
                    continue
                resolved_height = max(
                    int(self._attr_base_row_height),
                    int(table.rowHeight(row)),
                )
                for column in wrapped_columns:
                    item = table.item(row, column)
                    if item is None:
                        continue
                    # 委托的绘制区域会扣除焦点框、单元格边距和样式内边距；
                    # 使用更保守的净宽度，并额外预留一段基线空间，避免
                    # 高 DPI 下最后一行只显示上半截。
                    available_width = max(24, table.columnWidth(column) - 36)
                    text_height = self._wrapped_attr_text_height(
                        item.text(), available_width
                    )
                    line_height = max(1, QFontMetrics(table.font()).height())
                    resolved_height = max(
                        resolved_height,
                        text_height + max(22, line_height // 2 + 16),
                    )

                for column in range(table.columnCount()):
                    cell = table.cellWidget(row, column)
                    if cell is None:
                        continue
                    layout = cell.layout()
                    if layout is not None:
                        layout.activate()
                    resolved_height = max(
                        resolved_height,
                        int(cell.sizeHint().height()) + 8,
                        int(cell.minimumSizeHint().height()) + 8,
                    )
                table.setRowHeight(row, resolved_height)
        finally:
            table.setUpdatesEnabled(True)
            table.viewport().update()

    def _schedule_attr_row_resize(self, delay_ms: int = 80) -> None:
        """在列宽和 cellWidget 几何稳定后防抖重算全部属性行高。"""
        timer = getattr(self, "_attr_row_resize_timer", None)
        if timer is None:
            QTimer.singleShot(max(0, int(delay_ms)), self._resize_attr_rows_to_wrapped_content)
            return
        timer.setInterval(max(0, int(delay_ms)))
        timer.start()

    def _remeasure_attr_columns(self, columns=None) -> None:
        """重算指定属性列；``None`` 表示重算全部 8 列。"""
        table = getattr(self, "attr_table", None)
        if table is None:
            return
        if columns is None:
            selected = list(range(table.columnCount()))
        else:
            selected = sorted({int(value) for value in columns})
        header = table.horizontalHeader()
        table.setUpdatesEnabled(False)
        try:
            for column in selected:
                if column < 0 or column >= table.columnCount():
                    continue
                header.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
                table.setColumnWidth(column, self._measure_attr_column_width(column))
        finally:
            table.setUpdatesEnabled(True)
            table.viewport().update()
        if any(column in self._attr_wrapped_column_maximums for column in selected):
            self._resize_attr_rows_to_wrapped_content()
            self._schedule_attr_row_resize(0)
            QTimer.singleShot(160, self._resize_attr_rows_to_wrapped_content)
            # 产品切换前若曾记住旧的超宽侧栏，按新的内容理想宽度收回；
            # 否则名称列虽然已变窄，splitter 仍可能保留旧宽度造成大片空白。
            ideal_width = self._attr_ideal_width()
            remembered_width = self._panel_width_mem.get("attr")
            if remembered_width is not None and remembered_width > ideal_width:
                self._panel_width_mem["attr"] = ideal_width
        if getattr(self, "attr_card", None) is not None:
            self.attr_card.setMinimumWidth(min(self._attr_ideal_width(), 320))

    def _schedule_attr_column_remeasure(self, *columns: int) -> None:
        """以 250 ms 防抖合并高频列宽更新。"""
        if columns:
            self._pending_attr_remeasure_columns.update(int(col) for col in columns)
        else:
            self._pending_attr_remeasure_columns.update(
                range(self.attr_table.columnCount())
            )
        self._attr_column_remeasure_timer.start(250)

    def _apply_scheduled_attr_column_remeasure(self) -> None:
        columns = set(self._pending_attr_remeasure_columns)
        self._pending_attr_remeasure_columns.clear()
        if columns:
            self._remeasure_attr_columns(columns)
            self._schedule_attr_row_resize()
            self._schedule_lower_panel_rebalance()

    @staticmethod
    def _measure_table_column_width(table: TableWidget, column: int, minimum: int) -> int:
        """测量预置命令表的表头、文本和嵌入控件宽度。"""
        metrics = QFontMetrics(table.font())
        header_item = table.horizontalHeaderItem(column)
        width = metrics.horizontalAdvance(
            header_item.text() if header_item is not None else ""
        )
        contains_widget = False
        for row in range(table.rowCount()):
            item = table.item(row, column)
            if item is not None:
                width = max(width, metrics.horizontalAdvance(str(item.text() or "")))
            cell = table.cellWidget(row, column)
            if cell is not None:
                contains_widget = True
                layout = cell.layout()
                if layout is not None:
                    layout.activate()
                width = max(
                    width,
                    int(cell.sizeHint().width()),
                    int(cell.minimumSizeHint().width()),
                )
        return max(int(minimum), width + (16 if contains_widget else 28))

    def _apply_preset_table_widths(self) -> None:
        """保护预置命令表中的按钮/复选框，并保留窄面板滚动。"""
        poweron = getattr(self, "poweron_table", None)
        if poweron is not None:
            header = poweron.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            poweron.setColumnWidth(
                0, self._measure_table_column_width(poweron, 0, 58)
            )
            poweron.setColumnWidth(
                2, self._measure_table_column_width(poweron, 2, 92)
            )
            poweron.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        autoreply = getattr(self, "autoreply_table", None)
        if autoreply is not None:
            header = autoreply.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
            autoreply.setColumnWidth(
                0, self._measure_table_column_width(autoreply, 0, 58)
            )
            autoreply.setHorizontalScrollBarPolicy(
                Qt.ScrollBarPolicy.ScrollBarAsNeeded
            )

    def _attr_ideal_width(self) -> int:
        """实时属性面板完整显示表格所有列所需的理想宽度。"""
        table = getattr(self, "attr_table", None)
        if table is None:
            return 560
        header = table.horizontalHeader()
        width = sum(
            max(header.sectionSize(col), self._attr_column_minimums.get(col, 40))
            for col in range(table.columnCount())
        )
        # 卡片左右内边距(12*2) + 表格边框/纵向滚动条余量
        return width + 48

    def _preset_ideal_width(self) -> int:
        """预置命令面板的理想宽度，随侧栏字号同步扩展。"""
        return max(400, round(400 * self._side_font_point_size / 10.0))

    def _rebalance_lower_panels(self) -> None:
        """重新分配横向三栏：实时数据 | 实时属性 | 预置命令。

        原则：
        - 每个侧栏优先沿用“记忆宽度”（用户拖动过或上次打开时的宽度），
          这样打开/关闭其中一个面板不会把另一个面板的尺寸重置。
        - 面板首次打开（尚无记忆宽度）时使用理想宽度；实时属性面板
          会按表格所有列的总宽自适应拉伸，恰好完整显示内容。
        - 数据窗口占据剩余空间，并保证最小可用宽度；空间不足时
          按比例压缩侧栏，但不会把压缩后的值写回记忆，窗口变宽后可恢复。
        """
        splitter = getattr(self, "content_splitter", None)
        data_card = getattr(self, "data_card", None)
        attr_card = getattr(self, "attr_card", None)
        preset_card = getattr(self, "preset_card", None)
        if (
            splitter is None
            or data_card is None
            or attr_card is None
            or preset_card is None
        ):
            return

        if splitter.contentsRect().width() <= 1:
            # 页面可见但布局几何尚未完成时，稍后再分配；隐藏页不循环排队。
            if self.isVisible():
                QTimer.singleShot(50, self._rebalance_lower_panels)
            return

        # isHidden() 只反映控件自身是否被显式隐藏，不受父容器可见性影响。
        attr_on = not attr_card.isHidden()
        preset_on = not preset_card.isHidden()
        self._update_content_orientation()

        if splitter.orientation() == Qt.Orientation.Vertical:
            visible_side_count = int(attr_on) + int(preset_on)
            handle_count = max(0, visible_side_count)
            total_height = max(1, splitter.contentsRect().height() - splitter.handleWidth() * handle_count)
            if visible_side_count <= 0:
                splitter.setSizes([total_height, 0, 0])
                return
            data_height = min(total_height, max(120, int(total_height * 0.45)))
            remaining = max(1, total_height - data_height)
            attr_height = remaining // visible_side_count if attr_on else 0
            preset_height = remaining - attr_height if preset_on else 0
            if attr_on and not preset_on:
                attr_height = remaining
            if preset_on and not attr_on:
                preset_height = remaining
            self._rebalancing = True
            try:
                splitter.setSizes([data_height, attr_height, preset_height])
            finally:
                QTimer.singleShot(0, lambda: setattr(self, "_rebalancing", False))
            splitter.updateGeometry()
            self.layout().activate()
            self._relayout_data_bar()
            self._relayout_attr_header()
            self._relayout_autoreply_header()
            return

        total = max(1, splitter.contentsRect().width() - splitter.handleWidth() * 2)
        min_data = 440
        attr_card.setMinimumWidth(min(self._attr_ideal_width(), 320))
        preset_card.setMinimumWidth(min(self._preset_ideal_width(), 320))

        # 实时属性：优先记忆宽度，其次理想宽度（自适应展开全部列）。
        if attr_on:
            attr_width = self._panel_width_mem.get("attr") or self._attr_ideal_width()
            self._panel_width_mem["attr"] = attr_width
        else:
            attr_width = 0
        # 预置命令：优先记忆宽度，其次理想宽度。
        if preset_on:
            preset_width = self._panel_width_mem.get("preset") or self._preset_ideal_width()
            self._panel_width_mem["preset"] = preset_width
        else:
            preset_width = 0

        # 保证数据窗口最小宽度；空间不足时按比例压缩两个侧栏。
        side_total = attr_width + preset_width
        available = max(0, total - min_data)
        if side_total > available and side_total > 0:
            scale = available / side_total
            attr_width = int(attr_width * scale)
            preset_width = max(0, available - attr_width)

        data_width = max(1, total - attr_width - preset_width)

        self._rebalancing = True
        try:
            splitter.setSizes([data_width, attr_width, preset_width])
        finally:
            QTimer.singleShot(0, lambda: setattr(self, "_rebalancing", False))
        splitter.updateGeometry()
        self.layout().activate()
        QTimer.singleShot(0, self._relayout_data_bar)
        QTimer.singleShot(0, self._relayout_attr_header)
        QTimer.singleShot(0, self._relayout_autoreply_header)
        QTimer.singleShot(0, self._layout_common_commands)

    def _relayout_all_mcu(self) -> None:
        """页面几何稳定后统一执行 MCU 页的全部响应式重排。"""
        self._relayout_operation_bar()
        self._relayout_data_bar()
        self._relayout_attr_header()
        self._relayout_autoreply_header()
        self._layout_common_commands()
        QTimer.singleShot(0, self._rebalance_lower_panels)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._layout_resize_timer.start()

    def _apply_debounced_mcu_layout(self) -> None:
        self._relayout_operation_bar()
        self._relayout_data_bar()
        self._relayout_attr_header()
        self._relayout_autoreply_header()
        self._layout_common_commands()
        # 表格侧栏宽度改变后同步重算受控换行列与行高。
        self._remeasure_attr_columns((2, 3))
        self._schedule_attr_row_resize(0)
        QTimer.singleShot(160, self._resize_attr_rows_to_wrapped_content)
        QTimer.singleShot(0, self._rebalance_lower_panels)


    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        # 子卡片的 contentsRect 在同步 showEvent 阶段仍可能为 0，延迟两次重排。
        QTimer.singleShot(0, self._relayout_all_mcu)
        if not self._first_show_relayout_done:
            self._first_show_relayout_done = True
            QTimer.singleShot(100, self._relayout_all_mcu)
        # 本页拥有独立的固定 HEX 协议解析通道；不读取、不修改页面1的
        # HEX/ASCII 与原始/协议解析按钮状态。

        # 两页产品协议不共享；切回页签2时恢复页签2当前 JSON 产品。
        name = str(self.product_combo.currentText() or "").strip()
        if name and name != getattr(self._mw, "product_var", ""):
            self._mw._load_product_cfg(name)
            self.refresh_attr_table()
            self.refresh_current_values()
            self._refresh_preset_commands()

    # ------------------------------------------------------------------
    # Public interface used by gui.py
    # ------------------------------------------------------------------
    def sync_products(self, preferred: str | None = None) -> None:
        """页签2只列出由产品 JSON 导入的产品。"""
        sources = getattr(self._mw, "_product_sources", {}) or {}
        kinds = getattr(self._mw, "_product_kinds", {}) or {}
        names = [name for name in sources if kinds.get(name) == "json"]
        current = preferred or self.product_combo.currentText()
        if current not in names:
            current = names[0] if names else ""

        self._product_syncing = True
        try:
            self.product_combo.clear()
            self.product_combo.addItems(names)
            if current:
                index = self.product_combo.findText(current)
                if index >= 0:
                    self.product_combo.setCurrentIndex(index)
        finally:
            self._product_syncing = False

        self.manage_json_button.setEnabled(bool(names))

        if not current:
            self.attr_table.clearContents()
            self.attr_table.setRowCount(0)
            self.poweron_table.clearContents()
            self.poweron_table.setRowCount(0)
            self.autoreply_table.clearContents()
            self.autoreply_table.setRowCount(0)
            self._clear_grid()
            return

        # 仅在页签2当前可见时加载 JSON 产品；隐藏时保留页面1的 Word 配置。
        if self.isVisible():
            try:
                if getattr(self._mw, "product_var", "") != current:
                    self._mw._load_product_cfg(current)
            except Exception:
                pass
        if getattr(self._mw, "product_var", "") == current:
            self.refresh_attr_table()
            self.refresh_current_values()
            self._refresh_preset_commands()



    def on_data(
        self,
        result,
        raw,
        ts: float,
        is_tx: bool = False,
        auto_reply: bool | None = None,
    ) -> None:
        """Queue formatted text; a 40 ms timer performs one document update."""
        text = format_frame_display(
            result,
            raw,
            ts,
            is_tx=is_tx,
            auto_reply=bool(auto_reply),
            attr_center=self._mw.get_attr_center(),
        )
        lines = text.splitlines(keepends=True)
        raw_line = lines[0] if lines else text
        parsed_text = "".join(lines[1:])
        tag = "[TX]" if is_tx else "[RX]"
        tag_index = raw_line.find(tag)
        normal_color = PALETTE["text"]
        tag_color = _TX_TAG_COLOR if is_tx else _RX_TAG_COLOR
        parsed_color = _TX_PARSED_COLOR if is_tx else _RX_PARSED_COLOR

        segments: list[tuple[str, str, bool]] = []
        if tag_index >= 0:
            segments.append((raw_line[:tag_index], normal_color, False))
            segments.append((tag, tag_color, True))
            segments.append((raw_line[tag_index + len(tag):], normal_color, False))
        else:
            segments.append((raw_line, normal_color, False))
        if parsed_text:
            segments.append((parsed_text, parsed_color, False))

        self._pending_data_segments.extend(seg for seg in segments if seg[0])
        self._pending_data_chars += sum(len(seg[0]) for seg in segments)
        # Bound the pre-render queue if the GUI thread is temporarily busy.
        if self._pending_data_chars > 1_000_000:
            while self._pending_data_segments and self._pending_data_chars > 500_000:
                old_text, _, _ = self._pending_data_segments.popleft()
                self._pending_data_chars -= len(old_text)
            warning = "[提示] 实时数据显示积压过多，已丢弃最早的待显示文本；串口数据保存不受影响。\n"
            self._pending_data_segments.appendleft((warning, PALETTE["error"], True))
            self._pending_data_chars += len(warning)
        if not self._data_flush_timer.isActive():
            self._data_flush_timer.start()

    def _flush_data_batch(self) -> None:
        if not self._pending_data_segments:
            return
        segments = list(self._pending_data_segments)
        self._pending_data_segments.clear()
        self._pending_data_chars = 0

        scroll_bar = self.data_text.verticalScrollBar()
        saved_scroll_value = scroll_bar.value()
        cursor = QTextCursor(self.data_text.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        format_cache: dict[tuple[str, bool], QTextCharFormat] = {}
        for text, color, bold in segments:
            key = (color, bold)
            fmt = format_cache.get(key)
            if fmt is None:
                fmt = QTextCharFormat()
                fmt.setForeground(QColor(color))
                if bold:
                    fmt.setFontWeight(QFont.Weight.DemiBold.value)
                format_cache[key] = fmt
            cursor.insertText(text, fmt)

        if self._auto_scroll:
            self.data_text.setTextCursor(cursor)
            self.data_text.ensureCursorVisible()
        else:
            scroll_bar.setValue(saved_scroll_value)

    def refresh_current_values(self, attrids=None) -> None:
        """Refresh only requested IDs; ``None`` means a full value-only refresh."""
        center = self._mw.get_attr_center()
        if attrids is None:
            entries = center.get_all_attrs()
        else:
            entries = []
            for raw_id in dict.fromkeys(int(value) for value in attrids):
                entry = center.get_entry(raw_id)
                if entry is not None:
                    entries.append(entry)
        current_value_changed = False
        for entry in entries:
            row = self._attr_row_by_id.get(entry.attrid)
            if row is None:
                continue
            item = self.attr_table.item(row, 6)
            if item is not None:
                current_text = str(entry.current_value)
                if item.text() != current_text:
                    item.setText(current_text)
                    current_value_changed = True
        if current_value_changed:
            self._schedule_attr_column_remeasure(6)

    @staticmethod
    def _sorted_enum_items(enum_map: dict) -> list[tuple[str, str]]:
        items = [(str(key), str(value)) for key, value in (enum_map or {}).items()]

        def _key(item: tuple[str, str]):
            raw = item[0].strip()
            try:
                return (0, int(raw, 0))
            except (TypeError, ValueError):
                return (1, raw.lower())

        return sorted(items, key=_key)

    @staticmethod
    def _integer_bounds(typeid: int) -> tuple[int, int] | None:
        return {
            1: (-128, 127),
            2: (0, 255),
            3: (-32768, 32767),
            4: (0, 65535),
            5: (-2147483648, 2147483647),
            6: (0, 4294967295),
            7: (-9223372036854775808, 9223372036854775807),
            8: (0, 18446744073709551615),
            15: (-128, 127),
            16: (0, 255),
            17: (-32768, 32767),
            18: (0, 65535),
            19: (-2147483648, 2147483647),
            20: (0, 4294967295),
            21: (-9223372036854775808, 9223372036854775807),
            22: (0, 18446744073709551615),
        }.get(int(typeid))

    def _build_attr_send_widget(self, entry, old_text: str) -> tuple[QWidget, int]:
        """Create type-aware report controls and return (widget, row_height)."""
        send_cell = QWidget(self.attr_table)
        if entry.access == "只写":
            send_layout = QHBoxLayout(send_cell)
            send_layout.setContentsMargins(3, 2, 3, 2)
            unavailable = BodyLabel("不可上报", send_cell)
            unavailable.setEnabled(False)
            send_layout.addWidget(unavailable)
            send_layout.addStretch(1)
            return send_cell, self._attr_base_row_height

        enum_items = self._sorted_enum_items(entry.enum)
        if not enum_items and entry.typeid == 0:
            enum_items = [("0", "关闭"), ("1", "开启")]

        if enum_items:
            grid = QGridLayout(send_cell)
            grid.setContentsMargins(3, 2, 3, 2)
            grid.setHorizontalSpacing(5)
            grid.setVerticalSpacing(4)
            columns = 2 if len(enum_items) > 1 else 1
            grid_row = 0
            grid_column = 0
            for raw_value, description in enum_items:
                label = f"{raw_value}: {description}" if description and description != raw_value else raw_value
                button = PushButton(label, send_cell)
                button.setMinimumHeight(self._enum_button_height)
                fit_text_control(button, point_size=self._side_font_point_size)
                button.setToolTip(label)
                button.clicked.connect(
                    lambda checked=False, aid=entry.attrid, value=raw_value:
                    self._on_attr_send(aid, value)
                )

                # 长枚举说明独占一整行，短说明两列排列，效果接近功能
                # 属性网页中的枚举按钮，同时避免长文字被过度截断。
                full_row = columns == 2 and len(label) > 18
                if full_row:
                    if grid_column:
                        grid_row += 1
                        grid_column = 0
                    grid.addWidget(button, grid_row, 0, 1, 2)
                    grid_row += 1
                else:
                    grid.addWidget(button, grid_row, grid_column)
                    grid_column += 1
                    if grid_column >= columns:
                        grid_row += 1
                        grid_column = 0
            grid.setColumnStretch(0, 1)
            if columns > 1:
                grid.setColumnStretch(1, 1)
            row_count = grid_row + (1 if grid_column else 0)
            return send_cell, max(
                self._attr_base_row_height,
                row_count * (self._enum_button_height + 6) + 6,
            )

        send_layout = QHBoxLayout(send_cell)
        send_layout.setContentsMargins(3, 2, 3, 2)
        send_layout.setSpacing(5)
        send_edit = LineEdit(send_cell)
        bounds = self._integer_bounds(entry.typeid)
        constraints = self._mw.get_attr_center().get_value_constraints(entry.attrid)
        if bounds is not None:
            low, high = bounds
            try:
                product_min = constraints.get("minimum")
                product_max = constraints.get("maximum")
                if product_min not in (None, ""):
                    low = max(low, int(product_min))
                if product_max not in (None, ""):
                    high = min(high, int(product_max))
                bounds = (low, high)
            except (TypeError, ValueError):
                pass
        if bounds is not None:
            send_edit.setPlaceholderText(f"{bounds[0]}-{bounds[1]}")
            # QIntValidator 的底层范围是 32 位 int。UINT32/INT64/UINT64
            # 不能强塞进该验证器，否则会错误阻止协议类型本来允许的值；
            # 这些大整数仍会在发送前由 AttrStateCenter 做严格校验。
            if (
                -2147483648 <= bounds[0] <= 2147483647
                and -2147483648 <= bounds[1] <= 2147483647
            ):
                send_edit.setValidator(QIntValidator(bounds[0], bounds[1], send_edit))
        elif entry.typeid in (9, 10, 15, 16, 17, 18, 19, 20, 21, 22):
            send_edit.setPlaceholderText(str(entry.range_str or "数值"))
        elif entry.typeid == 11:
            send_edit.setPlaceholderText("字符串")
            max_length = constraints.get("string_length")
            if isinstance(max_length, int) and max_length > 0:
                send_edit.setMaxLength(max_length)
        elif entry.typeid in (13, 14, 23, 24):
            send_edit.setPlaceholderText("JSON")
        else:
            send_edit.setPlaceholderText("值")
        send_edit.setText(old_text)
        send_button = PushButton("上报", send_cell)
        fit_text_control(send_button, point_size=self._side_font_point_size)
        send_button.clicked.connect(
            lambda checked=False, aid=entry.attrid, edit=send_edit:
            self._on_attr_send(aid, edit.text())
        )
        send_layout.addWidget(send_edit, 1)
        send_layout.addWidget(send_button)
        self._attr_send_edits[entry.attrid] = send_edit
        return send_cell, self._attr_base_row_height

    def refresh_attr_table(self) -> None:
        center = self._mw.get_attr_center()
        old_send = {aid: edit.text() for aid, edit in self._attr_send_edits.items()}
        old_checked = {aid: check.isChecked() for aid, check in self._attr_select_checks.items()}
        entries = center.get_all_attrs()
        self.attr_table.setUpdatesEnabled(False)
        try:
            self.attr_table.clearContents()
            self.attr_table.setRowCount(len(entries))
            self._attr_row_by_id.clear()
            self._attr_send_edits.clear()
            self._attr_select_checks.clear()
            for row, entry in enumerate(entries):
                self._attr_row_by_id[entry.attrid] = row
                check = CheckBox(self.attr_table)
                reportable = entry.access != "只写"
                check.setChecked(reportable and old_checked.get(entry.attrid, True))
                check.setEnabled(reportable)
                if not reportable:
                    check.setToolTip("只写属性不能由 MCU 主动状态上报")
                check.stateChanged.connect(self._on_row_select_changed)
                check_cell = QWidget(self.attr_table)
                check_layout = QHBoxLayout(check_cell)
                check_layout.setContentsMargins(0, 0, 0, 0)
                check_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
                check_layout.addWidget(check)
                self.attr_table.setCellWidget(row, 0, check_cell)
                self._attr_select_checks[entry.attrid] = check

                id_item = self._readonly_item(f"0x{entry.attrid:02X}")
                id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.attr_table.setItem(row, 1, id_item)
                attribute_key = str(
                    getattr(entry, "source_attribute_key", "") or ""
                ).strip()
                attribute_name = str(
                    getattr(entry, "source_attribute_name", "") or ""
                ).strip()
                name_source = attribute_name or attribute_key or entry.cn_name or entry.name
                display_name = localized_attribute_name(
                    name_source,
                    fallback=str(entry.cn_name or f"属性0x{entry.attrid:02X}"),
                )
                if attribute_key and attribute_name and attribute_key != attribute_name:
                    property_text = f"{attribute_key} / {attribute_name}"
                else:
                    property_text = (
                        attribute_name
                        or attribute_key
                        or str(getattr(entry, "original_name", "") or entry.name or "")
                    )
                name_item = self._readonly_item(display_name)
                name_item.setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
                self.attr_table.setItem(row, 2, name_item)
                property_item = self._readonly_item(property_text)
                property_item.setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
                self.attr_table.setItem(row, 3, property_item)
                access_item = self._readonly_item(entry.access)
                access_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.attr_table.setItem(row, 4, access_item)
                type_item = self._readonly_item(_typeid_name(entry.typeid))
                type_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.attr_table.setItem(row, 5, type_item)
                current_item = self._readonly_item(entry.current_value)
                current_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.attr_table.setItem(row, 6, current_item)

                send_cell, row_height = self._build_attr_send_widget(
                    entry, old_send.get(entry.attrid, "")
                )
                self.attr_table.setCellWidget(row, 7, send_cell)
                self.attr_table.setRowHeight(row, row_height)
        finally:
            self.attr_table.setUpdatesEnabled(True)
            self.attr_table.viewport().update()
        self._sync_select_all_state()
        apply_table_font(
            self.attr_table,
            make_ui_font(self._side_font_point_size),
            minimum_padding=16,
        )
        apply_adaptive_geometry(self.attr_table, self._side_font_point_size)
        self.attr_table.horizontalHeader().setMinimumSectionSize(46)
        self._remeasure_attr_columns()
        self._resize_attr_rows_to_wrapped_content()
        self._schedule_attr_row_resize(0)
        QTimer.singleShot(160, self._resize_attr_rows_to_wrapped_content)
        # cellWidget 的最终 sizeHint 可能要到本轮布局结束后才稳定，再补一次
        # 防抖测量，保证枚举按钮组和“输入框+上报”完整显示。
        self._schedule_attr_column_remeasure()
        self._schedule_lower_panel_rebalance()

    # ------------------------------------------------------------------
    # Product import / switching
    # ------------------------------------------------------------------
    def _on_product_changed(self, name: str) -> None:
        if self._product_syncing or not name:
            return
        if getattr(self._mw, "_product_kinds", {}).get(name) != "json":
            return
        self._mw._load_product_cfg(name)
        self.refresh_attr_table()
        self.refresh_current_values()
        self._refresh_preset_commands()


    def _import_product_json(self) -> None:
        from protocol_parser.product_import_dialog import ProductImportDialog

        dialog = ProductImportDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self._save_product_from_dialog(dialog)

    def _edit_or_delete_product(self) -> None:
        """打开独立产品 JSON 管理窗口，可选择任意产品进行修改或删除。"""
        from protocol_parser.product_manage_dialog import ProductJsonManageDialog
        from protocol_parser.product_management import collect_product_json_records

        records = collect_product_json_records(
            getattr(self._mw, "_product_sources", {}) or {},
            getattr(self._mw, "_product_kinds", {}) or {},
        )
        if not records:
            StyledMessageBox.information(self, "提示", "当前没有可管理的产品 JSON")
            return

        active_name = str(self.product_combo.currentText() or "").strip()
        dialog = ProductJsonManageDialog(
            self,
            records,
            current_product=active_name,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        selected_name = str(dialog.selected_product or "").strip()
        source_path = dialog.selected_source_path
        if not selected_name or source_path is None:
            return

        # 接收数据时允许管理其他产品，但不能替换或删除当前正在参与
        # MCU 解析和自动回复的产品，避免工作线程持有半更新配置。
        editing_active_monitor_product = bool(
            getattr(self._mw, "is_collecting", False)
            and getattr(self._mw, "_monitoring_page", None) == 1
            and selected_name == active_name
        )
        if editing_active_monitor_product:
            action_text = "修改" if dialog.requested_action == dialog.ACTION_EDIT else "删除"
            StyledMessageBox.warning(
                self,
                "当前产品正在使用",
                f"产品“{selected_name}”正在参与模拟 MCU 接收和自动回复，"
                f"监控期间不能{action_text}当前产品。\n\n"
                "可以选择其他产品进行管理，或停止监控后再操作当前产品。",
            )
            return

        if dialog.requested_action == dialog.ACTION_EDIT:
            self._edit_selected_product(selected_name, source_path, active_name)
        elif dialog.requested_action == dialog.ACTION_DELETE:
            self._delete_selected_product(selected_name, source_path, active_name)

    def _edit_selected_product(
        self,
        product_name: str,
        source_path: Path,
        active_product: str,
    ) -> None:
        """编辑管理窗口中选中的产品，而不是固定编辑当前产品。"""
        if not source_path.exists():
            StyledMessageBox.warning(self, "提示", f"找不到产品“{product_name}”的 JSON 文件")
            return
        try:
            raw_cfg = json.loads(source_path.read_text(encoding="utf-8-sig"))
            if not isinstance(raw_cfg, dict):
                raise ProductConfigError("产品 JSON 顶层必须是对象")
        except Exception as exc:
            self._mw._report_error("读取所选产品失败", exc)
            return

        info = raw_cfg.get("product_info") if isinstance(raw_cfg.get("product_info"), dict) else {}
        version_raw = info.get("mcu_version", [1, 0, 0])
        if isinstance(version_raw, (list, tuple)):
            version_text = ".".join(str(part) for part in list(version_raw)[:3])
        else:
            version_text = str(version_raw or "1.0.0")
        source_json = raw_cfg.get("source_function_json")
        if source_json in (None, ""):
            source_json = raw_cfg.get("attributes") or {}

        from protocol_parser.product_import_dialog import ProductImportDialog

        dialog = ProductImportDialog(
            self,
            product_name=str(raw_cfg.get("product") or product_name),
            pid=str(info.get("pid") or ""),
            model=str(info.get("model") or ""),
            version=version_text,
            json_text=source_json,
            edit_mode=True,
            allow_delete=False,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        selected_is_active = product_name == active_product
        self._save_product_from_dialog(
            dialog,
            old_product_name=product_name,
            old_source_path=source_path,
            selected_attrids={
                str(key).upper()
                for key in (raw_cfg.get("attributes") or {})
                if not str(key).startswith("__")
            },
            activate_after_save=selected_is_active,
            preserve_product=active_product if not selected_is_active else "",
        )

    def _delete_selected_product(
        self,
        product_name: str,
        source_path: Path,
        active_product: str,
    ) -> None:
        """删除管理窗口中选中的产品，并保留其他正在使用的产品。"""
        selected_is_active = product_name == active_product
        if selected_is_active:
            detail = (
                "该产品是模拟 MCU 当前产品。删除后将自动切换到其他可用产品；"
                "若没有其他产品，当前产品和属性表将被清空。"
            )
        else:
            detail = (
                f"当前模拟 MCU 产品“{active_product}”不会切换，"
                "接收、解析和自动回复配置保持不变。"
                if active_product else "删除所选产品不会加载其他产品。"
            )
        answer = QMessageBox.question(
            self,
            "删除产品",
            f"确定删除产品“{product_name}”及其 JSON 文件吗？\n\n"
            f"{source_path.name}\n\n{detail}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        try:
            from protocol_parser.paths import mark_product_json_deleted

            # Register the deletion before refreshing the protocol list so any
            # bundled product deliberately removed by the user is not seeded
            # again during get_protocol_dir().
            mark_product_json_deleted(source_path.name)
            source_path.unlink()
            self._mw._load_protocols()

            if not selected_is_active and active_product:
                # 删除后台产品时恢复原选择，不重新加载或重建当前产品上下文。
                self.sync_products(active_product)
                self._mw._set_status(f"已删除产品JSON：{product_name}")
                return

            self.sync_products()
            next_name = str(self.product_combo.currentText() or "").strip()
            if next_name:
                self._mw._load_product_cfg(next_name)
                self.refresh_attr_table()
                self.refresh_current_values()
                self._refresh_preset_commands()
            else:
                self._mw.cfg = None
                self._mw._mcu_cfg = {}
                self._mw.product_var = ""
                self._mw.get_attr_center().load_product({})
                self._mw._sync_collector_cfg()
                self.refresh_attr_table()
                self._refresh_preset_commands()
            self._mw._set_status(f"已删除产品JSON：{product_name}")
        except Exception as exc:
            self._mw._report_error("删除产品JSON失败", exc)

    def _save_product_from_dialog(
        self,
        dialog,
        *,
        old_product_name: str = "",
        old_source_path: Path | None = None,
        selected_attrids: set[str] | None = None,
        activate_after_save: bool = True,
        preserve_product: str = "",
    ) -> None:
        from protocol_parser.attr_editor import AttributeEditorDialog
        from protocol_parser.product_importer import (
            build_product_cfg,
            extract_device_info_metadata,
            localize_attributes,
            parse_function_json,
            safe_protocol_filename,
            save_product_cfg,
        )

        try:
            attributes = parse_function_json(dialog.json_text)
            localize_attributes(attributes)
            product_name = dialog.product_name or dialog.model or dialog.pid or "未命名产品"
            product_name = str(product_name).strip()
            if not product_name:
                raise ProductConfigError("产品名称不能为空")

            existing_source = str(
                getattr(self._mw, "_product_sources", {}).get(product_name) or ""
            )
            if existing_source:
                same_source = False
                if old_source_path is not None:
                    try:
                        same_source = Path(existing_source).resolve() == old_source_path.resolve()
                    except Exception:
                        same_source = existing_source == str(old_source_path)
                if not same_source:
                    StyledMessageBox.warning(self, "提示", f"产品名称“{product_name}”已存在，请更换名称")
                    return

            source_metadata = extract_device_info_metadata(dialog.json_text)
            source_version = source_metadata.get("version") or []
            source_pid = str(source_metadata.get("pid") or "").strip()
            source_model = str(source_metadata.get("model") or "").strip()
            imported_cfg = build_product_cfg(
                product_name=product_name,
                pid=dialog.pid or source_pid,
                model=dialog.model or source_model,
                attributes=attributes,
                # Base.version is the authoritative 3-byte prefix of the 0x21
                # reply.  The dialog value remains a fallback for JSON formats
                # that do not carry device-information metadata.
                mcu_version=source_version or dialog.version,
            )
            imported_cfg["source_function_json"] = dialog.json_text
            if source_metadata.get("expand_rules"):
                imported_cfg["device_info_expand_rules"] = source_metadata["expand_rules"]
            if source_version:
                imported_cfg.setdefault("product_info", {})["device_info_version"] = list(source_version)

            # 修改现有产品时，若用户仍使用原来的 services JSON，重新解析会
            # 丢失先前从属性配置导出文件学到的 snapshot_wire_id/nowValue。
            # 按属性键继承这些隐藏协议元数据，避免保存后 0x24 又退回空字符串。
            old_cfg_for_metadata = None
            if old_source_path is not None and old_source_path.exists():
                try:
                    old_cfg_for_metadata = json.loads(
                        old_source_path.read_text(encoding="utf-8-sig")
                    )
                except Exception:
                    old_cfg_for_metadata = None
            if isinstance(old_cfg_for_metadata, dict):
                old_attrs = old_cfg_for_metadata.get("attributes") or {}
                for attr_key, new_meta in (imported_cfg.get("attributes") or {}).items():
                    old_meta = old_attrs.get(attr_key)
                    if not isinstance(new_meta, dict) or not isinstance(old_meta, dict):
                        continue
                    for passthrough_key in (
                        "snapshot_wire_id",
                        "initial_value",
                        "snapshot_include",
                        "source_data_rwx",
                        "source_data_type",
                        "source_attribute_key",
                        "source_attribute_name",
                    ):
                        if passthrough_key in old_meta and passthrough_key not in new_meta:
                            new_meta[passthrough_key] = old_meta.get(passthrough_key)
                if old_cfg_for_metadata.get("device_info_expand_rules") and not imported_cfg.get("device_info_expand_rules"):
                    imported_cfg["device_info_expand_rules"] = old_cfg_for_metadata.get(
                        "device_info_expand_rules"
                    )
                old_info = old_cfg_for_metadata.get("product_info") or {}
                if (
                    isinstance(old_info, dict)
                    and old_info.get("device_info_version")
                    and not imported_cfg.get("product_info", {}).get("device_info_version")
                ):
                    imported_cfg.setdefault("product_info", {})["device_info_version"] = list(
                        old_info.get("device_info_version")
                    )

            # ``extract_device_info_metadata`` above already normalizes and
            # validates Base.expandRules.  Do not regenerate the F3 mapping from
            # the selected attribute list when exact export bytes are available:
            # serial order/SIID/PIID in Base.expandRules are protocol data, not UI
            # ordering, and changing them produces an invalid 0x21 reply.

            editor = AttributeEditorDialog(
                self,
                imported_cfg,
                prefer_chinese_name=True,
                selected_attrids=selected_attrids,
            )
            if editor.exec() != QDialog.DialogCode.Accepted or not editor.result:
                return
            user_cfg = editor.result
            user_cfg["import_source"] = "json"
            user_cfg["product_info"] = dict(imported_cfg.get("product_info") or {})
            user_cfg["product"] = str(user_cfg.get("product") or product_name)
            user_cfg["source_function_json"] = dialog.json_text
            if imported_cfg.get("device_info_expand_rules"):
                user_cfg["device_info_expand_rules"] = imported_cfg.get("device_info_expand_rules")
            localize_attributes(user_cfg.get("attributes"))

            # 保存文件前先完成一次属性中心校验。这样枚举定义与类型矛盾、
            # 范围无合法值等真正的产品配置错误不会先落盘，再在重新加载时
            # 留下一个“已保存但导入失败”的半完成状态。
            from protocol_parser.attr_center import AttrStateCenter

            validation_center = AttrStateCenter()
            validation_center.load_product(user_cfg)
            for raw_attrid, meta in (user_cfg.get("attributes") or {}).items():
                if str(raw_attrid).startswith("__") or not isinstance(meta, dict):
                    continue
                try:
                    attrid = (
                        int(str(raw_attrid), 16)
                        if str(raw_attrid).lower().startswith("0x")
                        else int(raw_attrid)
                    )
                except (TypeError, ValueError):
                    continue
                entry = validation_center.get_entry(attrid)
                if entry is not None:
                    meta["initial_value"] = entry.current_value

            filename = safe_protocol_filename(user_cfg["product"], model=dialog.model, pid=dialog.pid)
            target_path = self._mw.get_protocol_dir() / filename
            if target_path.exists() and old_source_path is not None:
                try:
                    is_old_target = target_path.resolve() == old_source_path.resolve()
                except Exception:
                    is_old_target = str(target_path) == str(old_source_path)
                if not is_old_target:
                    StyledMessageBox.warning(self, "提示", f"目标文件 {target_path.name} 已存在，请修改 Model、PID 或产品名称")
                    return
            elif target_path.exists() and old_source_path is None:
                StyledMessageBox.warning(self, "提示", f"目标文件 {target_path.name} 已存在，请修改 Model、PID 或产品名称")
                return

            save_path = save_product_cfg(target_path, user_cfg)
            from protocol_parser.paths import clear_product_json_deleted

            # Explicit import/save restores a previously deleted filename and
            # therefore clears its suppression marker.
            clear_product_json_deleted(save_path.name)
            if old_source_path is not None:
                try:
                    renamed = save_path.resolve() != old_source_path.resolve()
                except Exception:
                    renamed = str(save_path) != str(old_source_path)
                if renamed and old_source_path.exists():
                    from protocol_parser.paths import mark_product_json_deleted

                    # Renaming a bundled product is also an intentional removal
                    # of the old filename; otherwise it would reappear at the
                    # next protocol refresh.
                    mark_product_json_deleted(old_source_path.name)
                    old_source_path.unlink()

            self._mw._load_protocols()
            selected_name = user_cfg["product"]
            if activate_after_save:
                self.sync_products(selected_name)
                if not self._mw._load_product_cfg(selected_name):
                    raise ProductConfigError("产品文件已保存，但重新加载校验失败")
            else:
                # 修改非当前产品时只刷新产品索引，保持正在接收数据的当前
                # 产品、属性中心、解析器和自动回复上下文完全不变。
                preferred = str(preserve_product or "").strip()
                self.sync_products(preferred if preferred else None)
            action = "已修改" if old_product_name else "已导入"
            self._mw._set_status(
                f"产品JSON{action}：{selected_name}（{save_path.name}）"
            )
        except Exception as exc:
            title = "产品JSON修改失败" if old_product_name else "产品JSON导入失败"
            self._mw._report_error(title, exc)

    # ------------------------------------------------------------------
    # Attribute actions
    # ------------------------------------------------------------------
    @staticmethod
    def _readonly_item(value: object) -> QTableWidgetItem:
        text = str(value)
        item = QTableWidgetItem(text)
        item.setToolTip(text)
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item

    def _on_attr_send(self, attrid: int, value_text: str) -> None:
        center = self._mw.get_attr_center()
        entry = center.get_entry(attrid)
        if entry is None:
            StyledMessageBox.warning(self, "提示", f"属性 0x{attrid:02X} 不存在")
            return
        if entry.access == "只写":
            StyledMessageBox.warning(self, "提示", "只写属性不能由 MCU 主动状态上报")
            return
        try:
            value = _convert_value(value_text, entry.typeid)
            value = center.validate_attr_value(attrid, value)
            frame = self._mw.get_cmd_engine().build_attr_report([attrid], {attrid: value})
            if self._mw._send_generated_frame(frame, entry.cn_name or entry.name):
                center.set_attr_value(attrid, value)
                self.refresh_current_values()
        except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
            # 枚举、范围、步长、字符串长度或输入格式不符合产品定义，
            # 属于可预期的用户输入问题：只显示橙色提示，不写 error.log，
            # 也不能使用“程序遇到未知错误”的严重故障弹窗。
            StyledMessageBox.warning(
                self,
                "属性值不符合要求",
                format_attr_validation_message(
                    entry,
                    value_text,
                    center.get_value_constraints(attrid),
                    exc,
                ),
            )
        except Exception as exc:
            # 串口、编码器或其他非预期异常才按程序故障记录。
            self._mw._report_error("属性发送失败", exc)

    def _on_batch_report(self) -> None:
        center = self._mw.get_attr_center()
        selected: list[tuple[int, object]] = []
        try:
            for entry in center.get_all_attrs():
                if entry.access == "只写":
                    continue
                check = self._attr_select_checks.get(entry.attrid)
                if not (check and check.isChecked()):
                    continue
                # 批量发送前再次进行产品级校验，避免历史缓存或外部修改
                # 留下超范围值后被直接编码并发送。
                value = center.validate_attr_value(entry.attrid, entry.current_value)
                selected.append((entry.attrid, value))
            if not selected:
                StyledMessageBox.information(self, "提示", "请至少勾选一个要批量上报的属性")
                return
            frame = self._mw.get_cmd_engine().build_attr_report(
                [aid for aid, _ in selected], {aid: value for aid, value in selected}
            )
            if self._mw._send_generated_frame(frame, "MCU-批量上报"):
                for aid, value in selected:
                    center.set_attr_value(aid, value)
                self.refresh_current_values()
        except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
            StyledMessageBox.warning(
                self,
                "批量上报内容不符合要求",
                f"所选属性中存在不能上报的值：\n\n{exc}\n\n请修改对应属性值后重试。",
            )
        except Exception as exc:
            self._mw._report_error("批量上报失败", exc)

    # ------------------------------------------------------------------
    # Preset commands / auto reply
    # ------------------------------------------------------------------
    def _select_preset_index(self, index: int) -> None:
        self.preset_stack.setCurrentIndex(max(0, min(index, self.preset_stack.count() - 1)))

    def _clear_grid(self) -> None:
        while self.common_layout.count():
            item = self.common_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _refresh_preset_commands(self) -> None:
        self._refresh_poweron_commands()
        self._refresh_autoreply_rules()
        self._refresh_common_commands()
        font = make_ui_font(self._side_font_point_size)
        apply_table_font(self.poweron_table, font, minimum_padding=16)
        apply_table_font(self.autoreply_table, font, minimum_padding=16)
        self._apply_preset_table_widths()

    def _refresh_poweron_commands(self) -> None:
        engine = self._mw.get_cmd_engine()
        commands: list[tuple[str, Callable[[], bytes]]] = [
            ("回复心跳(首次)", lambda: engine.build_heartbeat_resp(True)),
            ("回复设备信息 (0x21)", engine.build_dev_info_resp),
            ("回复设备快照 (0x24)", engine.build_snapshot_resp),
            ("发起配网 (0x23)", lambda: engine.build_net_config(1)),
            ("请求时间 (0x26)", engine.build_time_request),
        ]
        self._poweron_builders = commands
        self.poweron_table.setRowCount(len(commands))
        for row, (name, builder) in enumerate(commands):
            self.poweron_table.setItem(row, 0, self._readonly_item(row + 1))
            self.poweron_table.setItem(row, 1, self._readonly_item(name))
            button = PushButton("发送", self.poweron_table)
            fit_text_control(button, point_size=self._side_font_point_size)
            button.clicked.connect(
                lambda checked=False, b=builder, label=name: self._send_preset_builder(b, label)
            )
            cell = QWidget(self.poweron_table)
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(3, 2, 3, 2)
            cell_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell_layout.addWidget(button)
            self.poweron_table.setCellWidget(row, 2, cell)
            self.poweron_table.setRowHeight(row, self._preset_row_height)
        apply_adaptive_geometry(self.poweron_table, self._side_font_point_size)
        self._apply_preset_table_widths()

    def _refresh_autoreply_rules(self) -> None:
        engine = self._mw.get_auto_reply()
        rules = engine.rules
        self.auto_reply_switch.blockSignals(True)
        self.auto_reply_switch.setChecked(engine.enabled)
        self.auto_reply_switch.blockSignals(False)
        self.autoreply_table.setRowCount(len(rules))
        for row, (cmd_code, rule) in enumerate(sorted(rules.items())):
            check = CheckBox(self.autoreply_table)
            check.setChecked(rule.enabled)
            check.stateChanged.connect(
                lambda state, code=cmd_code: engine.set_rule_enabled(
                    code,
                    state == Qt.CheckState.Checked
                    or (isinstance(state, int) and state == 2),
                )
            )
            cell = QWidget(self.autoreply_table)
            cell_layout = QHBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            cell_layout.addWidget(check)
            self.autoreply_table.setCellWidget(row, 0, cell)
            self.autoreply_table.setItem(row, 1, self._readonly_item(rule.name))
            self.autoreply_table.setItem(row, 2, self._readonly_item(rule.description))
            self.autoreply_table.setRowHeight(row, self._preset_row_height)
        apply_adaptive_geometry(self.autoreply_table, self._side_font_point_size)
        self._apply_preset_table_widths()

    def _refresh_common_commands(self) -> None:
        self._clear_grid()
        engine = self._mw.get_cmd_engine()
        commands: list[tuple[str, Callable[[], bytes]]] = [
            ("上报全部属性", engine.build_attr_report),
            ("发起配网", lambda: engine.build_net_config(1)),
            ("请求时间", engine.build_time_request),
            ("进入低功耗", lambda: engine.build_mcu_status(1)),
            ("退出低功耗", lambda: engine.build_mcu_status(0)),
        ]
        self._common_command_buttons = []
        for name, builder in commands:
            button = PushButton(name, self.common_page)
            fit_text_control(button, point_size=self._side_font_point_size)
            button.clicked.connect(
                lambda checked=False, b=builder, label=name: self._send_preset_builder(b, label)
            )
            self._common_command_buttons.append(button)

        self.common_separator = QFrame(self.common_page)
        self.common_separator.setFrameShape(QFrame.Shape.HLine)
        self.common_low_power_title = BodyLabel("低功耗唤醒模拟", self.common_page)

        self.btn_lp_service = PushButton("开启低功耗服务", self.common_page)
        fit_text_control(self.btn_lp_service, point_size=self._side_font_point_size)
        self.btn_lp_service.clicked.connect(self._on_toggle_lp_service)

        self.btn_io_wake = PrimaryPushButton("IO 唤醒", self.common_page)
        fit_text_control(self.btn_io_wake, point_size=self._side_font_point_size)
        self.btn_io_wake.clicked.connect(self._on_io_wake)

        self.lbl_lp_status = BodyLabel("低功耗状态: 正常", self.common_page)
        self.lbl_lp_status.setStyleSheet("color: green;")
        self.lbl_lp_status.setWordWrap(True)
        self._common_all_widgets = (
            *self._common_command_buttons,
            self.common_separator,
            self.common_low_power_title,
            self.btn_lp_service,
            self.btn_io_wake,
            self.lbl_lp_status,
        )
        self._layout_common_commands()

    def _layout_common_commands(self) -> None:
        layout = getattr(self, "common_layout", None)
        widgets = getattr(self, "_common_all_widgets", ())
        if layout is None or not widgets:
            return
        for widget in widgets:
            layout.removeWidget(widget)
        page = getattr(self, "common_page", None)
        width = int(page.contentsRect().width()) if page is not None else 0
        if width <= 1:
            width = int(page.width()) if page is not None else 0
        if width <= 1:
            width = self._preset_ideal_width()
        columns = 3 if width >= 600 else (2 if width >= 380 else 1)
        row = 0
        for index, button in enumerate(self._common_command_buttons):
            row = index // columns
            layout.addWidget(button, row, index % columns)
        row = (len(self._common_command_buttons) + columns - 1) // columns
        layout.addWidget(self.common_separator, row, 0, 1, columns)
        row += 1
        layout.addWidget(self.common_low_power_title, row, 0, 1, columns)
        row += 1
        if columns >= 3:
            layout.addWidget(self.btn_lp_service, row, 0)
            layout.addWidget(self.btn_io_wake, row, 1)
            layout.addWidget(self.lbl_lp_status, row, 2)
        elif columns == 2:
            layout.addWidget(self.btn_lp_service, row, 0)
            layout.addWidget(self.btn_io_wake, row, 1)
            layout.addWidget(self.lbl_lp_status, row + 1, 0, 1, 2)
            row += 1
        else:
            layout.addWidget(self.btn_lp_service, row, 0)
            layout.addWidget(self.btn_io_wake, row + 1, 0)
            layout.addWidget(self.lbl_lp_status, row + 2, 0)
            row += 2
        for column in range(columns):
            layout.setColumnStretch(column, 1)
        layout.setRowStretch(row + 1, 1)
        layout.invalidate()

    def _on_toggle_lp_service(self) -> None:
        btn = self.btn_lp_service
        if "开启" in btn.text():
            frame = self._mw.get_cmd_engine().build_low_power_service(enable=True)
            btn.setText("关闭低功耗服务")
        else:
            frame = self._mw.get_cmd_engine().build_low_power_service(enable=False)
            btn.setText("开启低功耗服务")
        self._send_preset_builder(lambda: frame, "低功耗服务")

    def _on_io_wake(self) -> None:
        """IO 唤醒模拟：50ms 拉高 + 50ms 预留 → 首次心跳返回 0x00

        V3.0 协议低功耗唤醒流程：
        1. IO 引脚拉高 50ms（模拟电平唤醒信号）
        2. 预留 50ms 等待模组启动
        3. 模组重启后发送心跳，MCU 回复 0x00（重启标志）
        4. 后续心跳恢复正常回复 0x01
        """
        collector = self._mw.get_collector()
        if not (collector and getattr(collector, "running", False)):
            StyledMessageBox.warning(self, "提示", "请先开始监控")
            return
        auto_reply = self._mw.get_auto_reply()
        cmd = self._mw.get_cmd_engine()

        self.btn_io_wake.setEnabled(False)
        self.btn_io_wake.setText("唤醒中...")

        # Step 1: 发送退出低功耗（模拟 IO 拉高后模组唤醒）
        exit_frame = cmd.build_low_power_exit()
        if exit_frame:
            collector.send(exit_frame)

        # Step 2: 50ms 后发送首次心跳（重启标志 0x00）
        def _send_reset_heartbeat():
            current = self._mw.get_collector()
            if not (current and getattr(current, "running", False)):
                self._mw._set_status("IO 唤醒已取消：串口已停止")
                return
            try:
                reset_frame = auto_reply.wake()
                if reset_frame:
                    current.send(reset_frame)
                self._update_lp_status(False)
            except Exception as exc:
                self._mw._set_status(f"IO 唤醒失败：{exc}")
                try:
                    self._mw._on_ui_error(f"IO 唤醒失败：{exc}")
                except Exception:
                    pass

        # Step 3: 100ms 后恢复按钮
        def _restore_button():
            self.btn_io_wake.setEnabled(True)
            self.btn_io_wake.setText("IO 唤醒")

        QTimer.singleShot(50, _send_reset_heartbeat)
        QTimer.singleShot(100, _restore_button)

    def _update_lp_status(self, in_low_power: bool) -> None:
        if in_low_power:
            self.lbl_lp_status.setText("低功耗状态: 休眠中（心跳已停止）")
            self.lbl_lp_status.setStyleSheet("color: orange;")
        else:
            self.lbl_lp_status.setText("低功耗状态: 正常")
            self.lbl_lp_status.setStyleSheet("color: green;")

    def _send_preset_builder(self, builder: Callable[[], bytes], label: str) -> None:
        try:
            self._mw._send_generated_frame(builder(), label)
        except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
            StyledMessageBox.warning(
                self,
                "预置命令参数不符合要求",
                f"命令“{label}”的参数不符合当前产品定义：\n\n{exc}",
            )
        except Exception as exc:
            self._mw._report_error("预置命令发送失败", exc)

    def _on_poweron_send_all(self) -> None:
        timer = getattr(self, "_poweron_timer", None)
        if timer is not None and timer.isActive():
            self._mw._set_status("上电流程正在发送，请勿重复点击")
            return
        collector = self._mw.get_collector()
        if not (collector and getattr(collector, "running", False)):
            StyledMessageBox.warning(self, "提示", "请先开始监控")
            return
        # 用 QTimer 间隔发送，避免瞬间打满串口缓冲
        self._poweron_queue = list(self._poweron_builders)
        if not self._poweron_queue:
            return
        self._poweron_sent = 0
        self.btn_poweron_send_all.setEnabled(False)
        self._poweron_timer = QTimer(self)
        self._poweron_timer.setInterval(300)
        self._poweron_timer.timeout.connect(self._poweron_send_next)
        self._poweron_timer.start()
        # 立即发送第一条
        self._poweron_send_next()

    def _poweron_send_next(self) -> None:
        if not getattr(self, "_poweron_queue", None):
            if hasattr(self, "_poweron_timer") and self._poweron_timer:
                self._poweron_timer.stop()
                self._poweron_timer = None
            self.btn_poweron_send_all.setEnabled(True)
            return
        collector = self._mw.get_collector()
        if not (collector and getattr(collector, "running", False)):
            if hasattr(self, "_poweron_timer") and self._poweron_timer:
                self._poweron_timer.stop()
                self._poweron_timer = None
            self.btn_poweron_send_all.setEnabled(True)
            return
        label, builder = self._poweron_queue.pop(0)
        try:
            collector.send(builder())
            self._poweron_sent += 1
            self._mw._set_status(f"上电流程: 已发送 {self._poweron_sent}/{len(self._poweron_builders)}")
        except (ValueError, TypeError, UnicodeError, OverflowError) as exc:
            StyledMessageBox.warning(
                self,
                "上电命令参数不符合要求",
                f"命令“{label}”未发送：\n\n{exc}",
            )
        except Exception as exc:
            self._mw._report_error("上电流程发送失败", exc)
        if not self._poweron_queue:
            self._poweron_timer.stop()
            self._poweron_timer = None
            self.btn_poweron_send_all.setEnabled(True)
            self._mw._set_status(f"上电流程已发送 {self._poweron_sent} 条命令")

    def _on_auto_reply_toggled(self, checked: bool) -> None:
        # 自动回复使用独立 MCU HEX 通道，与页面1显示模式完全无关。
        # 每次重新打开全局开关时恢复全部协议规则，避免某条规则曾被
        # 取消勾选后长期静默，出现“心跳能回、命令不回”的误判。
        engine = self._mw.get_auto_reply()
        engine.enable(bool(checked), enable_all_rules=bool(checked))
        if checked:
            self._refresh_autoreply_rules()
        self._mw._set_status("自动回复已开启（全部规则）" if checked else "自动回复已关闭")

    def _clear_data(self) -> None:
        self._data_flush_timer.stop()
        self._pending_data_segments.clear()
        self._pending_data_chars = 0
        self.data_text.clear()
