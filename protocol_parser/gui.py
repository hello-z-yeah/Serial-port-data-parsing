"""协议解析工具 GUI（PySide6 + qfluentwidgets Fluent 风格）。

【重构说明】
- 仅替换界面框架与事件绑定（Tkinter → PySide6 + qfluentwidgets）
- protocol_parser 模块、SerialCollector、组帧/拆帧/校验、文件读写等业务逻辑 100% 原样保留
- UI → 业务层入参与回调接口保持完全一致
"""
from __future__ import annotations

import os
import sys
import ctypes
from ctypes import wintypes
import threading
import time
import json
import re
import inspect
import html
import weakref
from collections import deque
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from PySide6.QtCore import (
    Qt, QTimer, Signal, Slot, QObject, QSize, QUrl
)
from PySide6.QtGui import (
    QFont, QFontMetrics, QTextCursor, QTextCharFormat, QColor, QDesktopServices, QIcon, QPen, QGuiApplication
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QSplitter, QFrame, QLabel, QSizePolicy, QFileDialog, QMessageBox,
    QAbstractItemView, QHeaderView, QTableWidgetItem, QMenu,
    QDialog, QDialogButtonBox, QFormLayout, QSpinBox as QtSpinBox,
    QStyledItemDelegate, QStyleOptionViewItem, QListView, QAbstractButton,
    QStackedWidget, QButtonGroup, QBoxLayout, QScrollArea, QLayout,
)
from qfluentwidgets import (
    FluentWindow, setTheme, Theme, setThemeColor,
    PrimaryPushButton, PushButton, LineEdit, ComboBox, EditableComboBox,
    CardWidget, BodyLabel, StrongBodyLabel,
    TextEdit, CheckBox, SpinBox, TableWidget,
    FluentIcon, ToggleButton, Pivot,
)

# 让 exe 也能找到 protocol_parser 包
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from protocol_parser import (  # noqa: E402
    APP_NAME,
    VERSION,
    ParseResult,
    ProtocolError,
    classify_protocol_error,
    _log_error_to_disk,
    load_protocol,
    parse_frame,
    parse_hex_input,
    to_hex,
)
from protocol_parser.serial_collector import FrameSynchronizer, SerialCollector  # noqa: E402
from protocol_parser.app_info import APP_ID  # noqa: E402
from protocol_parser.storage import RawDataWriter  # noqa: E402
from protocol_parser.exceptions import (  # noqa: E402
    CommandValidationError,
    StorageOperationError,
    UserCorrectableError,
)
from protocol_parser.paths import (  # noqa: E402
    resource_path,
    user_data_path,
    get_protocol_dir,
    write_crash_log as _write_crash_log_gui,
)
from protocol_parser.theme import ThemeManager, PALETTE  # noqa: E402
from protocol_parser.widgets import (  # noqa: E402
    apply_tooltip, TwoOptionSegmentSwitch, StyledMessageBox, apply_fluent_dialog_style,
)
from protocol_parser.ui_error import build_user_error_presentation  # noqa: E402
from protocol_parser.attr_center import AttrStateCenter  # noqa: E402
from protocol_parser.auto_cmd import AutoCmdEngine  # noqa: E402
from protocol_parser.auto_reply import AutoReplyEngine  # noqa: E402
from protocol_parser.receive_page import ReceiveAnalysisPage  # noqa: E402
from protocol_parser.mcu_page import McuSimulatePage, CtrlWheelZoomTextEdit  # noqa: E402
from protocol_parser.combo_font import (  # noqa: E402
    MatchedPopupComboBox,
    MatchedPopupEditableComboBox,
)
from protocol_parser.dpi_font import (  # noqa: E402
    UI_FONT_FAMILY,
    UI_FONT_BASE_POINT_SIZE,
    effective_resolution_scale,
    responsive_point_size,
    make_ui_font,
    apply_application_font,
    apply_adaptive_geometry,
    fit_text_control,
    fit_window_to_screen,
    install_adaptive_ui_controller,
)

# 统一所有提示/警告/错误弹窗的 Fluent 外观；保留原 QMessageBox 静态调用接口。
_QtMessageBox = QMessageBox  # 保留原生 QMessageBox，供“是/否”确认框使用
QMessageBox = StyledMessageBox


# Windows 下使用明确的 UI 字体和整数点值，避免系统回退字体与分数缩放
# 造成小字号文字发虚。数据窗口也继承同一字体。
_UI_FONT_FAMILY = UI_FONT_FAMILY
_UI_FONT_POINT_SIZE = UI_FONT_BASE_POINT_SIZE
# 标题栏使用标准逻辑尺寸。Qt 会按系统 DPI 自动把 pt 字体和逻辑像素
# 映射到实际像素，因此这里不再叠加 1.5/1.8 倍缩放，避免高分辨率下过大。
_COMPACT_TITLE_BAR_HEIGHT = 32
_TITLE_FONT_POINT_SIZE = _UI_FONT_POINT_SIZE
_TITLE_ICON_SIZE = 18
_WINDOW_BUTTON_WIDTH = 42
_WINDOW_BUTTON_ICON_SIZE = 16

# 指令库与主界面使用同一字号；表头只通过字重区分。Qt 的点值字体
# 自身会随 DPI 适配，不再依据屏幕分辨率额外放大。
_CMDLIB_FONT_BASE_SIZE = _UI_FONT_POINT_SIZE
_CMDLIB_FONT_MAX_SIZE = _UI_FONT_POINT_SIZE

# 指令库操作列和行内发送按钮使用更宽的逻辑尺寸。这里使用 Qt
# 逻辑像素，系统 DPI 缩放会自动映射到实际像素。
_CMDLIB_ACTION_COLUMN_WIDTH = 128
_CMDLIB_SEND_BUTTON_MIN_WIDTH = 96
_CMDLIB_SEND_BUTTON_MIN_HEIGHT = 28

# 弹窗逻辑尺寸。避免 setFixedSize，以便系统 DPI 或更大字体时布局
# 能按 sizeHint 继续扩展，不裁切文字。
_ADD_SERIAL_DIALOG_MIN_WIDTH = 600
_ADD_SERIAL_PORT_COMBO_MIN_WIDTH = 440


def _effective_ui_scale(widget: QWidget | None = None) -> float:
    """Return the shared logical-resolution scale used by all UI pages."""
    return effective_resolution_scale(widget)


def _responsive_point_size(
    widget: QWidget | None = None,
    *,
    base: int = _UI_FONT_POINT_SIZE,
    maximum: int = 14,
) -> int:
    """Return a point-size that remains readable on 2K/4K logical desktops."""
    return responsive_point_size(widget, base=base, maximum=maximum)


def _font_metrics(widget: QWidget, fallback_size: int | None = None) -> QFontMetrics:
    font = QFont(widget.font())
    if font.pointSizeF() <= 0:
        font.setPointSize(fallback_size or _UI_FONT_POINT_SIZE)
    return QFontMetrics(font)


def _fit_button_to_text(
    button: QWidget,
    *,
    horizontal_padding: int = 24,
    vertical_padding: int = 10,
    minimum_width: int = 0,
    minimum_height: int = 0,
) -> tuple[int, int]:
    """Size a button from its label and current font without clipping."""
    metrics = _font_metrics(button)
    text_getter = getattr(button, "text", None)
    text = str(text_getter() if callable(text_getter) else "")
    hint = button.sizeHint()
    width = max(
        minimum_width,
        int(hint.width()),
        metrics.horizontalAdvance(text) + horizontal_padding,
    )
    height = max(
        minimum_height,
        int(hint.height()),
        metrics.height() + vertical_padding,
    )
    button.setMinimumSize(width, height)
    # A finite maximum inherited from a previous DPI pass must not crop text.
    if button.maximumWidth() < width:
        button.setMaximumWidth(16_777_215)
    if button.maximumHeight() < height:
        button.setMaximumHeight(16_777_215)
    button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
    return width, height


def _apply_cmdlib_table_font_style(table: QWidget, font: QFont) -> None:
    """Apply a normal-size, DPI-aware command-library stylesheet.

    Point-size fonts and Qt logical pixels are already DPI aware.  Keeping the
    table at the application font size prevents the previous resolution-based
    double scaling on 2K/4K screens.
    """
    point_size = max(1, round(font.pointSizeF()))
    qss = f"""
    QTableWidget#CommandLibraryTable {{
        font-family: "{_UI_FONT_FAMILY}";
        font-size: {point_size}pt;
    }}
    QTableWidget#CommandLibraryTable QHeaderView::section {{
        font-family: "{_UI_FONT_FAMILY}";
        font-size: {point_size}pt;
        font-weight: 600;
        padding: 3px 5px;
    }}
    QPushButton[commandTableButton="true"] {{
        font-family: "{_UI_FONT_FAMILY}";
        font-size: {point_size}pt;
        padding: 2px 10px;
    }}
    """
    base_qss = table.property("_cmdlib_base_stylesheet")
    if base_qss is None:
        base_qss = table.styleSheet()
        table.setProperty("_cmdlib_base_stylesheet", base_qss)
    table.setStyleSheet(str(base_qss or "") + qss)


def _is_qt_widget_alive(widget: QWidget | None) -> bool:
    """Best-effort validity check for delayed Qt callbacks."""
    if widget is None:
        return False
    try:
        import shiboken6
        return bool(shiboken6.isValid(widget))
    except Exception:
        try:
            return not bool(widget.property("_destroyed"))
        except Exception:
            return False


def _schedule_combo_popup_font_sync(combo: QWidget) -> None:
    """Schedule per-instance popup styling without retaining a destroyed widget.

    This intentionally uses a weak reference and a bound per-instance call.  It
    avoids the classic global class-binding closure problem where every popup
    accidentally points at the first combo box created.
    """
    combo_ref = weakref.ref(combo)

    def _apply() -> None:
        current = combo_ref()
        if not _is_qt_widget_alive(current):
            return
        _sync_combo_popup_font(current)

    QTimer.singleShot(0, _apply)


def _sync_combo_popup_font(combo: QWidget) -> None:
    """Keep qfluentwidgets popup text equal to the combo-box text."""
    font = QFont(combo.font())
    if font.pointSizeF() <= 0:
        font.setPointSize(_responsive_point_size(combo, maximum=13))
        combo.setFont(font)

    menu = getattr(combo, "dropMenu", None)
    if menu is None:
        return

    widgets: list[QWidget] = [menu]
    try:
        widgets.extend(menu.findChildren(QWidget))
    except Exception:
        pass
    for child in widgets:
        try:
            child.setFont(font)
        except Exception:
            pass

    # RoundMenu may expose action widgets that are not returned by view lookup.
    # Apply the exact combo font to those widgets as well.
    try:
        for action in menu.actions():
            action.setFont(font)
            widget_for_action = getattr(menu, "widgetForAction", None)
            if callable(widget_for_action):
                action_widget = widget_for_action(action)
                if action_widget is not None:
                    action_widget.setFont(font)
    except Exception:
        pass

    view = getattr(menu, "view", None)
    if view is None:
        try:
            view = menu.findChild(QListView)
        except Exception:
            view = None
    if view is not None:
        try:
            view.setFont(font)
            metrics = QFontMetrics(font)
            row_height = max(combo.height(), metrics.height() + 14)
            signature = f"{font.family()}|{font.pointSizeF():g}|{font.weight()}|{row_height}"
            if view.property("_combo_popup_font_signature") != signature:
                view.setProperty("_combo_popup_font_signature", signature)
                view.setStyleSheet(
                    view.styleSheet()
                    + f"""
                    QListView, QListWidget {{
                        font-family: \"{font.family()}\";
                        font-size: {font.pointSizeF():g}pt;
                        font-weight: {font.weight()};
                    }}
                    QListView::item, QListWidget::item {{
                        min-height: {row_height}px;
                        padding-left: 10px;
                        padding-right: 10px;
                    }}
                    """
                )
        except Exception:
            pass


def _make_crisp_ui_font(point_size: int = _UI_FONT_POINT_SIZE) -> QFont:
    return make_ui_font(point_size)






# 存储开启时使用与“停止监控”一致的主色按钮视觉；关闭后恢复普通按钮。
_STORAGE_ACTIVE_QSS = f"""
QPushButton {{
    color: white;
    background-color: {PALETTE["primary"]};
    border: 1px solid {PALETTE["primary"]};
    border-radius: 5px;
    padding: 5px 12px;
}}
QPushButton:hover {{
    background-color: {PALETTE["primary_hover"]};
    border-color: {PALETTE["primary_hover"]};
}}
QPushButton:pressed {{
    background-color: #005A9E;
    border-color: #005A9E;
}}
QPushButton:disabled {{
    color: #FFFFFF;
    background-color: #8ABDE3;
    border-color: #8ABDE3;
}}
"""


_TEXT_EDIT_FRAME_QSS = f"""
QTextEdit {{
    color: {PALETTE["text"]};
    background-color: {PALETTE["card_bg"]};
    border: 1px solid {PALETTE["card_border"]};
    border-radius: 6px;
    padding: 6px;
}}
QTextEdit:focus {{
    border: 1px solid {PALETTE["primary"]};
}}
"""

_COMMAND_EDITOR_QSS = f"""
QLineEdit {{
    color: {PALETTE["text"]};
    background-color: {PALETTE["card_bg"]};
    border: 1px solid {PALETTE["card_border"]};
    border-radius: 5px;
    padding: 2px 6px;
}}
QLineEdit:focus {{
    border: 1px solid {PALETTE["primary"]};
}}
"""


class DpiAwareComboBox(MatchedPopupComboBox):
    """Fluent combo whose popup inherits the exact same font and row scale."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(_make_crisp_ui_font(_responsive_point_size(self, maximum=13)))
        # Popup font synchronization is provided by MatchedPopupComboBox.


class ToggleCloseEditableComboBox(MatchedPopupEditableComboBox):
    """可编辑下拉框：再次点击箭头时可靠收回菜单。

    qfluentwidgets 的 ``RoundMenu`` 是独立弹窗。在 Windows 上点击已经
    展开的箭头时，弹窗会先按“点击外部区域”关闭，随后箭头按钮的
    ``clicked`` 信号又把菜单重新打开，因此视觉上像是无法收回。
    这里记录菜单刚关闭的时刻，并吞掉同一次鼠标点击产生的重新打开。
    """

    _REOPEN_GUARD_SECONDS = 0.18

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(_make_crisp_ui_font(_responsive_point_size(self, maximum=13)))
        self._drop_menu_closed_at = 0.0

        # 替换基类直接连接到 _toggleComboMenu 的处理，避免关闭后立刻重开。
        try:
            self.dropButton.clicked.disconnect()
        except (TypeError, RuntimeError):
            pass
        self.dropButton.clicked.connect(self._on_drop_button_clicked)

    def _onDropMenuClosed(self) -> None:
        self._drop_menu_closed_at = time.monotonic()
        self.dropMenu = None

    def _on_drop_button_clicked(self, checked: bool = False) -> None:
        del checked

        # 菜单仍存在时，当前点击就是明确的“收回”。
        if self.dropMenu is not None:
            self._closeComboMenu()
            return

        # Windows 会先关闭弹窗、再把同一次点击传给箭头按钮。阻止这次重开。
        if time.monotonic() - self._drop_menu_closed_at < self._REOPEN_GUARD_SECONDS:
            return

        self._showComboMenu()

    def _showComboMenu(self) -> None:
        super()._showComboMenu()


# ---------- 资源/数据路径（统一委托给 protocol_parser.paths） ----------

def _move_to_recycle_bin(path: Path) -> bool:
    """把文件移入 Windows 回收站（可恢复），成功返回 True。"""
    if not sys.platform.startswith("win"):
        return False
    try:
        class _SHFILEOPSTRUCTW(ctypes.Structure):
            _fields_ = [
                ("hwnd", ctypes.c_void_p),
                ("wFunc", ctypes.c_uint),
                ("pFrom", ctypes.c_wchar_p),
                ("pTo", ctypes.c_wchar_p),
                ("fFlags", ctypes.c_ushort),
                ("fAnyOperationsAborted", ctypes.c_int),
                ("hNameMappings", ctypes.c_void_p),
                ("lpszProgressTitle", ctypes.c_wchar_p),
            ]

        FO_DELETE = 3
        FOF_ALLOWUNDO = 0x0040
        FOF_NOCONFIRMATION = 0x0010
        FOF_SILENT = 0x0004
        op = _SHFILEOPSTRUCTW()
        op.hwnd = None
        op.wFunc = FO_DELETE
        op.pFrom = str(Path(path).resolve()) + "\0"  # 双空结尾
        op.pTo = None
        op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
        return result == 0 and not op.fAnyOperationsAborted
    except Exception:
        return False


def load_builtin_protocol() -> dict:
    from protocol_parser.parser import load_protocol as _load
    external_dir = get_protocol_dir()
    external_file = external_dir / "v3_serial.json"
    if external_file.exists():
        try:
            return _load(external_file)
        except ProtocolError:
            pass
    bundled = resource_path("product") / "v3_serial.json"
    if bundled.exists():
        try:
            return _load(bundled)
        except ProtocolError:
            pass
    return {"product": "串口3.0协议", "description": "内置基础协议", "commands": [], "frame": {}, "enums": {}, "attributes": {}}


_builtin_v3: dict | None = None


def get_builtin_v3(refresh: bool = False) -> dict:
    global _builtin_v3
    if refresh or _builtin_v3 is None:
        _builtin_v3 = load_builtin_protocol()
    return _builtin_v3


# ---------- 线程安全信号桥（替代 Tk after + queue） ----------

class CommandLibraryCellDelegate(QStyledItemDelegate):
    """保留 Fluent 表格绘制，并为指令库提供主题化编辑框与可选分隔线。

    注意：本委托只通过 ``setItemDelegateForColumn`` 安装。表格自身的
    Fluent 委托仍保留在 ``TableWidget.delegate``，因此悬停行处理所需的
    ``setHoverRow`` 不会被覆盖。
    """

    def __init__(
        self,
        table: TableWidget,
        base_delegate,
        *,
        draw_right_separator: bool = False,
    ) -> None:
        super().__init__(table)
        self._base_delegate = base_delegate
        self._draw_right_separator = bool(draw_right_separator)

    def paint(self, painter, option: QStyleOptionViewItem, index) -> None:
        if self._base_delegate is not None:
            self._base_delegate.paint(painter, option, index)
        else:
            super().paint(painter, option, index)

        if not self._draw_right_separator:
            return

        painter.save()
        painter.setPen(QPen(QColor(PALETTE["card_border"]), 1))
        x = option.rect.right()
        painter.drawLine(x, option.rect.top(), x, option.rect.bottom())
        painter.restore()

    def createEditor(self, parent, option, index):
        editor = LineEdit(parent)
        editor.setObjectName("CommandLibraryCellEditor")
        editor.setClearButtonEnabled(False)
        try:
            editor.setFont(self.parent().font())
        except Exception:
            pass
        editor.setStyleSheet(_COMMAND_EDITOR_QSS)
        return editor

    def setEditorData(self, editor, index) -> None:
        editor.setText(str(index.data(Qt.ItemDataRole.EditRole) or ""))
        editor.selectAll()

    def setModelData(self, editor, model, index) -> None:
        model.setData(index, editor.text(), Qt.ItemDataRole.EditRole)

    def updateEditorGeometry(self, editor, option, index) -> None:
        editor.setGeometry(option.rect.adjusted(3, 2, -3, -2))


class UiBridge(QObject):
    """把串口线程回调安全投递到主线程。"""
    frame_signal = Signal(object, float)          # ParseResult, ts
    raw_signal = Signal(bytes, float)             # data, ts
    error_signal = Signal(str)                    # 普通解析/回调错误
    collector_error_signal = Signal(int, str, str)  # generation, message, kind
    tx_signal = Signal(bytes, float, object)      # data_sent, ts, metadata
    status_signal = Signal(str)                   # status text
    attr_updated_signal = Signal(object)          # changed attr IDs
    mcu_data_signal = Signal(object, object, float, bool, bool)  # result, raw, ts, is_tx, auto_reply
    storage_error_signal = Signal(str)
    storage_drop_signal = Signal(int)
    collector_stopped_signal = Signal(int, object, object)  # generation, callback, error


class CycleOrderTable(TableWidget):
    """循环发送配置表：支持拖动整行调整发送顺序。"""

    rowMoveRequested = Signal(int, int)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropOverwriteMode(False)
        self.setDragDropMode(QAbstractItemView.InternalMove)
        self.setDefaultDropAction(Qt.MoveAction)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        source_row = self.currentRow()
        if source_row < 0:
            event.ignore()
            return

        try:
            pos = event.position().toPoint()
        except AttributeError:
            pos = event.pos()

        index = self.indexAt(pos)
        if index.isValid():
            target_row = index.row()
            if pos.y() > self.visualRect(index).center().y():
                target_row += 1
        else:
            target_row = self.rowCount()

        # 删除源行后，位于其下方的目标索引需要前移一位。
        if target_row > source_row:
            target_row -= 1
        target_row = max(0, min(target_row, self.rowCount() - 1))

        if target_row != source_row:
            self.rowMoveRequested.emit(source_row, target_row)

        event.acceptProposedAction()


# ---------- 指令库「配置循环发送」对话框 ----------

class CycleConfigDialog(QDialog):
    """勾选指令、设置独立间隔，并通过鼠标拖动调整发送顺序。"""

    def __init__(
        self,
        parent: QWidget | None,
        items: list[dict],
        seq: list[dict],
        is_hex: bool,
    ):
        super().__init__(parent)
        apply_fluent_dialog_style(self)
        self.setWindowTitle("配置循环发送")
        self._cycle_font = _make_crisp_ui_font(_UI_FONT_POINT_SIZE)
        self.setFont(self._cycle_font)
        self.resize(720, 480)
        self.setMinimumSize(600, 380)
        self._items = items
        self._seq = list(seq)
        self._is_hex = is_hex
        self.result_seq: list[dict] | None = None

        pool = {it.get("id"): it for it in items if it.get("id")}
        seq_map = {s.get("id"): s for s in self._seq}
        ordered_ids: list[str] = []
        for s in self._seq:
            iid = s.get("id")
            if iid in pool and iid not in ordered_ids:
                ordered_ids.append(iid)
        for it in items:
            iid = it.get("id")
            if iid and iid not in ordered_ids:
                ordered_ids.append(iid)
        self._ordered_ids = ordered_ids
        self._pool = pool
        self._seq_map = seq_map

        self._build_ui()
        apply_adaptive_geometry(self, _UI_FONT_POINT_SIZE)
        fit_window_to_screen(
            self,
            preferred=(760, 520),
            minimum=(560, 360),
            margin=(36, 72),
        )

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)

        intro_label = BodyLabel(
            "勾选参与循环的指令；每条指令可设置独立间隔(ms)；按住任意一行拖动即可调整发送顺序。"
        )
        intro_label.setFont(self._cycle_font)
        intro_label.setWordWrap(True)
        layout.addWidget(intro_label)

        body = QHBoxLayout()
        self.table = CycleOrderTable()
        self.table.setFont(self._cycle_font)
        self.table.rowMoveRequested.connect(self._move_row)
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["参与", "名称", "指令数据", "间隔(ms)"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        cycle_header = self.table.horizontalHeader()
        cycle_header.setSectionResizeMode(2, QHeaderView.Stretch)
        header_font = QFont(self._cycle_font)
        header_font.setWeight(QFont.Weight.DemiBold)
        cycle_header.setFont(header_font)
        cycle_metrics = QFontMetrics(self._cycle_font)
        self._cycle_row_height = max(32, cycle_metrics.height() + 12)
        cycle_header.setMinimumHeight(max(30, QFontMetrics(header_font).height() + 10))
        self.table.verticalHeader().setDefaultSectionSize(self._cycle_row_height)
        self.table.verticalHeader().setMinimumSectionSize(self._cycle_row_height)
        self.table.setColumnWidth(0, max(50, cycle_metrics.horizontalAdvance("参与") + 24))
        self.table.setColumnWidth(1, max(140, cycle_metrics.horizontalAdvance("名称") + 80))
        self.table.setColumnWidth(3, max(100, cycle_metrics.horizontalAdvance("间隔(ms)") + 28))
        self.table.setRowCount(len(self._ordered_ids))

        for row, cid in enumerate(self._ordered_ids):
            it = self._pool.get(cid) or {}
            on = cid in self._seq_map
            delay = str((self._seq_map.get(cid) or {}).get("delay_ms", 1000))

            chk = CheckBox()
            chk.setChecked(on)
            cell = QWidget()
            lay = QHBoxLayout(cell)
            lay.setContentsMargins(8, 0, 0, 0)
            lay.addWidget(chk)
            lay.addStretch()
            self.table.setCellWidget(row, 0, cell)
            # 保存 checkbox 引用
            cell._chk = chk  # type: ignore

            name_item = QTableWidgetItem(it.get("name") or "")
            name_item.setFont(self._cycle_font)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            name_item.setData(Qt.UserRole, cid)
            self.table.setItem(row, 1, name_item)

            payload_item = QTableWidgetItem(it.get("payload") or "")
            payload_item.setFont(self._cycle_font)
            payload_item.setFlags(payload_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 2, payload_item)

            delay_item = QTableWidgetItem(delay)
            delay_item.setFont(self._cycle_font)
            self.table.setItem(row, 3, delay_item)
            self.table.setRowHeight(row, self._cycle_row_height)

        body.addWidget(self.table, stretch=1)

        # 右侧排序按钮
        side = QVBoxLayout()
        for text, slot in [
            ("上移 ↑", lambda: self._move(-1)),
            ("下移 ↓", lambda: self._move(1)),
            ("置顶", lambda: self._move_edge(True)),
            ("置底", lambda: self._move_edge(False)),
            ("全选", lambda: self._toggle_all(True)),
            ("全不选", lambda: self._toggle_all(False)),
        ]:
            b = PushButton(text)
            b.setFont(self._cycle_font)
            _fit_button_to_text(b, horizontal_padding=22, vertical_padding=10, minimum_height=30)
            b.clicked.connect(slot)
            side.addWidget(b)
        side.addStretch()
        body.addLayout(side)
        layout.addLayout(body, stretch=1)

        bf = QHBoxLayout()
        bf.addStretch()
        btn_save = PrimaryPushButton("保存")
        btn_save.setFont(self._cycle_font)
        _fit_button_to_text(btn_save, horizontal_padding=22, vertical_padding=10, minimum_height=30)
        btn_save.clicked.connect(self._on_save)
        bf.addWidget(btn_save)
        btn_cancel = PushButton("取消")
        btn_cancel.setFont(self._cycle_font)
        _fit_button_to_text(btn_cancel, horizontal_padding=22, vertical_padding=10, minimum_height=30)
        btn_cancel.clicked.connect(self.reject)
        bf.addWidget(btn_cancel)
        layout.addLayout(bf)

    def _selected_row(self) -> int:
        rows = self.table.selectionModel().selectedRows()
        return rows[0].row() if rows else -1

    def _row_records(self) -> list[dict]:
        records: list[dict] = []
        for row in range(self.table.rowCount()):
            cell = self.table.cellWidget(row, 0)
            checked = bool(cell and hasattr(cell, "_chk") and cell._chk.isChecked())
            name_item = self.table.item(row, 1)
            payload_item = self.table.item(row, 2)
            delay_item = self.table.item(row, 3)
            records.append({
                "id": name_item.data(Qt.UserRole) if name_item else "",
                "checked": checked,
                "name": name_item.text() if name_item else "",
                "payload": payload_item.text() if payload_item else "",
                "delay": delay_item.text() if delay_item else "1000",
            })
        return records

    def _populate_records(self, records: list[dict]) -> None:
        self.table.setRowCount(len(records))
        for row, record in enumerate(records):
            chk = CheckBox()
            chk.setChecked(bool(record.get("checked")))
            cell = QWidget()
            lay = QHBoxLayout(cell)
            lay.setContentsMargins(8, 0, 0, 0)
            lay.addWidget(chk)
            lay.addStretch()
            cell._chk = chk  # type: ignore[attr-defined]
            self.table.setCellWidget(row, 0, cell)

            name_item = QTableWidgetItem(str(record.get("name") or ""))
            name_item.setFont(self._cycle_font)
            name_item.setFlags(name_item.flags() & ~Qt.ItemIsEditable)
            name_item.setData(Qt.UserRole, record.get("id") or "")
            self.table.setItem(row, 1, name_item)

            payload_item = QTableWidgetItem(str(record.get("payload") or ""))
            payload_item.setFont(self._cycle_font)
            payload_item.setFlags(payload_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 2, payload_item)
            delay_item = QTableWidgetItem(str(record.get("delay") or "1000"))
            delay_item.setFont(self._cycle_font)
            self.table.setItem(row, 3, delay_item)
            self.table.setRowHeight(row, getattr(self, "_cycle_row_height", 32))

    def _move_row(self, source_row: int, target_row: int) -> None:
        records = self._row_records()
        if not (0 <= source_row < len(records) and 0 <= target_row < len(records)):
            return
        record = records.pop(source_row)
        records.insert(target_row, record)
        self._populate_records(records)
        self.table.selectRow(target_row)

    def _move(self, delta: int) -> None:
        row = self._selected_row()
        if row < 0:
            return
        target = row + delta
        if 0 <= target < self.table.rowCount():
            self._move_row(row, target)

    def _move_edge(self, to_top: bool) -> None:
        row = self._selected_row()
        if row < 0:
            return
        target = 0 if to_top else self.table.rowCount() - 1
        if row != target:
            self._move_row(row, target)

    def _toggle_all(self, on: bool) -> None:
        for r in range(self.table.rowCount()):
            w = self.table.cellWidget(r, 0)
            if w and hasattr(w, "_chk"):
                w._chk.setChecked(on)

    def _on_save(self) -> None:
        new_seq = []
        for r in range(self.table.rowCount()):
            w = self.table.cellWidget(r, 0)
            if not (w and hasattr(w, "_chk") and w._chk.isChecked()):
                continue
            name_item = self.table.item(r, 1)
            cid = name_item.data(Qt.UserRole) if name_item else None
            if not cid:
                continue
            delay_item = self.table.item(r, 3)
            try:
                d = max(10, int((delay_item.text() if delay_item else "1000").strip()))
            except Exception:
                d = 1000
            new_seq.append({"id": cid, "delay_ms": d})
        self.result_seq = new_seq
        self.accept()


class AddSerialPortDialog(QDialog):
    """与主界面一致的添加串口对话框。"""

    def __init__(self, parent: QWidget | None, ports: list[dict]):
        super().__init__(parent)
        apply_fluent_dialog_style(self)
        self.setWindowTitle("添加串口")
        self._dialog_font = _make_crisp_ui_font(_UI_FONT_POINT_SIZE)
        self.setFont(self._dialog_font)
        self.setMinimumSize(_ADD_SERIAL_DIALOG_MIN_WIDTH, 260)
        self.resize(660, 280)
        self._ports = list(ports)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(0)

        card = CardWidget(self)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(14)

        heading = StrongBodyLabel("添加串口", card)
        heading_font = QFont(self._dialog_font)
        heading_font.setWeight(QFont.Weight.DemiBold)
        heading.setFont(heading_font)
        card_layout.addWidget(heading)

        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(12)

        port_label = BodyLabel("串口：", card)
        port_label.setFont(self._dialog_font)
        form.addWidget(port_label, 0, 0)
        self.port_combo = DpiAwareComboBox(card)
        self.port_combo.setFont(self._dialog_font)
        port_texts: list[str] = []
        for port in self._ports:
            device = str(port.get("device") or "")
            description = str(port.get("description") or "")
            text = f"{device} - {description}" if description and description != device else device
            port_texts.append(text)
            self.port_combo.addItem(text)
        combo_metrics = QFontMetrics(self._dialog_font)
        longest_port = max((combo_metrics.horizontalAdvance(t) for t in port_texts), default=0)
        port_combo_width = max(_ADD_SERIAL_PORT_COMBO_MIN_WIDTH, min(560, longest_port + 56))
        self.port_combo.setMinimumWidth(port_combo_width)
        self.port_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        form.addWidget(self.port_combo, 0, 1)

        baud_label = BodyLabel("波特率：", card)
        baud_label.setFont(self._dialog_font)
        form.addWidget(baud_label, 1, 0)
        self.baud_combo = ToggleCloseEditableComboBox(card)
        self.baud_combo.setFont(self._dialog_font)
        self.baud_combo.addItems([
            "9600", "115200", "460800", "921600", "1000000", "2000000"
        ])
        self.baud_combo.setCurrentText("9600")
        form.addWidget(self.baud_combo, 1, 1)
        form.setColumnStretch(1, 1)
        card_layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 4, 0, 0)
        buttons.setSpacing(8)
        buttons.addStretch(1)
        cancel_button = PushButton("取消", card)
        cancel_button.setFont(self._dialog_font)
        _fit_button_to_text(cancel_button, horizontal_padding=28, vertical_padding=12, minimum_width=88)
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button)
        ok_button = PrimaryPushButton("确定", card)
        ok_button.setFont(self._dialog_font)
        _fit_button_to_text(ok_button, horizontal_padding=28, vertical_padding=12, minimum_width=88)
        ok_button.clicked.connect(self.accept)
        buttons.addWidget(ok_button)
        card_layout.addLayout(buttons)

        outer.addWidget(card)
        apply_adaptive_geometry(self, _UI_FONT_POINT_SIZE)
        fit_window_to_screen(
            self,
            preferred=(700, 320),
            minimum=(520, 260),
            margin=(36, 72),
        )

    def selected_port(self) -> str:
        return self.port_combo.currentText().split(" - ")[0].strip()

    def selected_baud(self) -> int:
        text = self.baud_combo.currentText().strip()
        try:
            value = int(text)
        except (TypeError, ValueError) as exc:
            raise ValueError("波特率必须是正整数") from exc
        if value <= 0:
            raise ValueError("波特率必须是正整数")
        return value

    def accept(self) -> None:  # type: ignore[override]
        try:
            self.selected_baud()
        except ValueError as exc:
            QMessageBox.warning(self, "提示", str(exc))
            try:
                self.baud_combo.setFocus()
                # EditableComboBox 本身就是可编辑 LineEdit。
                self.baud_combo.selectAll()
            except Exception:
                pass
            return
        super().accept()


class ProtocolParserApp(FluentWindow):
    """主界面：FluentWindow + 业务逻辑原样保留。"""

    def __init__(self, monitor_port: str | None = None, monitor_baud: int = 9600):
        # 必须在 QApplication 已创建后才能实例化任何 QWidget / FluentWindow
        if QApplication.instance() is None:
            raise RuntimeError("ProtocolParserApp 必须在 QApplication 创建之后再实例化")

        super().__init__()
        self._current_title_bar_height = max(
            _COMPACT_TITLE_BAR_HEIGHT,
            QFontMetrics(QApplication.font()).height() + 10,
        )
        self.setWindowTitle(f"{APP_NAME} v{VERSION}")
        # 先按当前显示器的可用逻辑区域约束窗口。旧版固定最小
        # 1100x700，在 1366x768 + 125/150% 缩放时会大于桌面工作区，
        # 整个布局只能被系统裁切，表现为按钮和文字只显示一半。
        fit_window_to_screen(
            self,
            preferred=(1400, 860),
            minimum=(1000, 640),
            margin=(24, 56),
        )
        # fit_window_to_screen 已把 1000x640 与当前显示器可用逻辑区域取小值。
        # 不再无条件覆盖，否则 1366x768@150% 下最小窗口会大于桌面工作区。

        # 窗口在当前屏幕居中显示
        _screen = self.screen()
        if _screen is not None:
            _avail = _screen.availableGeometry()
            self.move(
                _avail.x() + (_avail.width() - self.width()) // 2,
                _avail.y() + (_avail.height() - self.height()) // 2,
            )

        # 主题管理器（仅提供 PALETTE.get；真正 setTheme 已在 main() 里、创建窗口前完成）
        self.theme = ThemeManager(mode="light", style="win11")

        # 启动参数
        self._monitor_port = monitor_port
        self._monitor_baud = monitor_baud

        # ---------- 业务状态（与原版完全一致） ----------
        self.cfg: dict | None = None
        # 模拟 MCU 页面保留自己的 JSON 产品协议配置，独立于页面1当前
        # Word 协议及其 HEX/ASCII 显示选择。
        self._mcu_cfg: dict = {}
        self.product_var = ""
        self.port_var = ""
        self.baudrate_var = "9600"
        self.baudrate_var_last_valid = "9600"
        self.bytesize_var = 8
        self.stopbits_var = 1
        self.collector: SerialCollector | None = None
        self.is_collecting = False
        self.serial_sender = "模组发送"

        # 串口连接代次与自动重连。每次启动/停止都会推进 generation，
        # 旧读取线程迟到的错误或数据不会再影响当前的新连接。
        self._collector_generation = 0
        self._serial_manual_stop = False
        self._serial_reconnect_attempt = 0
        self._serial_reconnect_max_attempts = 10
        self._serial_reconnect_params: dict | None = None
        self._serial_reconnect_reason = ""
        self._serial_stopping = False
        self._stopping_collector: SerialCollector | None = None
        self._serial_reconnect_timer = QTimer(self)
        self._serial_reconnect_timer.setSingleShot(True)
        self._serial_reconnect_timer.timeout.connect(self._attempt_serial_reconnect)

        # 角色、属性状态和自动指令/回复引擎。底层 parser.py 与
        # serial_collector.py 保持不变，所有扩展均在 GUI 上层封装。
        self._current_role = "mcu"
        self._attr_center = AttrStateCenter()
        self._cmd_engine = AutoCmdEngine(self._attr_center)
        self._auto_reply = AutoReplyEngine(
            collector=None,
            cmd_engine=self._cmd_engine,
            attr_center=self._attr_center,
            on_error=lambda message: self.bridge.error_signal.emit(message)
            if hasattr(self, "bridge") else None,
        )
        self._message_id = 0

        # 默认使用 ASCII 原始数据显示；HEX/协议解析由用户手动开启。
        self.hex_format = False
        self.detail_mode = False
        self.autoscroll = True
        self.view_mode = "raw"  # protocol | raw

        self.log_path: Path | None = None
        self.log_file = None
        self.log_count = 0
        self.rx_frame_count = 0
        self.tx_frame_count = 0

        # 原始数据保存
        self.save_raw_enabled = False
        self.save_raw_path = str(user_data_path())
        self.save_raw_filename = datetime.now().strftime("serial_data_%Y%m%d_%H%M%S")
        self.save_raw_file = None  # compatibility alias; writer owns the handle
        self.save_raw_current_size = 0
        self.raw_auto_split_mb = 50
        self.save_raw_max_size = 50 * 1024 * 1024
        self.save_raw_count = 0
        self._save_raw_active = False
        self._save_raw_as_ascii = True
        self._raw_writer: RawDataWriter | None = None
        self._raw_writer_drop_count = 0

        # 发送
        self.send_mode = "protocol"  # protocol / raw_hex / raw_ascii
        self.tx_cmd_code = "0x20"
        self.tx_direction = "模组发送"
        self.tx_fields = '{"value": 1}'
        self.tx_raw = ""
        self.tx_cycle = False
        self.tx_interval_ms = 1000
        self._tx_cycle_timer: QTimer | None = None
        self.tx_auto_crc8 = False
        self.tx_append_crlf = False
        self.tx_crc_algo = "ADD8"
        self._tx_input_validation_timer = QTimer(self)
        self._tx_input_validation_timer.setSingleShot(True)
        self._tx_input_validation_timer.setInterval(250)
        self._tx_input_validation_timer.timeout.connect(
            self._validate_send_input_nonmodal
        )

        # 指令库
        self.CMDLIB_MAX = 40
        self._cmdlib_mode = "hex"
        self._cmdlib_hex: list[dict] = []
        self._cmdlib_ascii: list[dict] = []
        self._cmdlib_cycle_hex: list[dict] = []
        self._cmdlib_cycle_ascii: list[dict] = []
        self._cmdlib_cycle_on = False
        self._cmdlib_cycle_timer: QTimer | None = None
        self._cmdlib_cycle_idx = 0
        self._cmdlib_pending_save: dict[str, list] = {}
        self._cmdlib_save_timer = QTimer(self)
        self._cmdlib_save_timer.setSingleShot(True)
        self._cmdlib_save_timer.setInterval(250)
        self._cmdlib_save_timer.timeout.connect(self._cmdlib_flush_pending_save)

        # 控制实时文本的内存占用。QTextDocument 会自动淘汰最早的文本块，
        # 避免长时间运行后窗口越来越卡。
        self.max_display_lines = 10000
        self._disp_line_count = 0
        self._display_utf8_bytes = 0
        self._last_status_message = ""
        self._startup_ready = False
        self._first_show_relayout_done = False

        # 主分栏尺寸变化采用防抖，窗口拖动时不为每一个像素重复布局。
        self._splitter_rebalance_timer = QTimer(self)
        self._splitter_rebalance_timer.setSingleShot(True)
        self._splitter_rebalance_timer.setInterval(80)
        self._splitter_rebalance_timer.timeout.connect(self._rebalance_main_splitter)

        # Window drags generate a resize event for almost every pixel.  Merge
        # expensive toolbar/grid reassembly into one pass after the geometry
        # settles, while the native window frame itself remains responsive.
        self._layout_resize_timer = QTimer(self)
        self._layout_resize_timer.setSingleShot(True)
        self._layout_resize_timer.setInterval(70)
        self._layout_resize_timer.timeout.connect(self._apply_debounced_resize_layout)

        # 状态栏刷新采用单次延迟合并，避免高频接收时重复统计整个文本窗口。
        self._status_refresh_timer = QTimer(self)
        self._status_refresh_timer.setSingleShot(True)
        self._status_refresh_timer.timeout.connect(self._refresh_status_bar)

        # 信号桥
        self.bridge = UiBridge()
        self.bridge.frame_signal.connect(self._on_ui_frame)
        self.bridge.raw_signal.connect(self._on_ui_raw)
        self.bridge.error_signal.connect(self._on_ui_error)
        self.bridge.collector_error_signal.connect(self._on_collector_error)
        self.bridge.tx_signal.connect(self._on_ui_tx)
        self.bridge.attr_updated_signal.connect(self._on_attr_updated)
        self.bridge.mcu_data_signal.connect(self._on_mcu_data)
        self.bridge.storage_error_signal.connect(self._on_storage_error)
        self.bridge.storage_drop_signal.connect(self._on_storage_drop)
        self.bridge.collector_stopped_signal.connect(self._on_collector_stopped)

        # 显示缓冲在 UI 构建前准备好。每个元素为 (文本, 颜色)。
        self._disp_buf: list[tuple[str, str | None]] = []
        self._disp_buf_chars = 0
        self._disp_flush_timer = QTimer(self)
        self._disp_flush_timer.setSingleShot(True)
        self._disp_flush_timer.setInterval(50)
        self._disp_flush_timer.timeout.connect(self._flush_display_buf)

        # 定时器：端口热插拔。首次串口扫描结束后才启动，避免首屏前阻塞。
        self._port_watch_timer = QTimer(self)
        self._port_watch_timer.timeout.connect(self._poll_ports)

        # 两个导航页面在 UI 构建时初始化。
        self.mcu_page = None
        self.receive_page = None

        # 构建首屏。协议文件解析、串口扫描和产品数据延后到事件循环启动后。
        self._build_ui()
        QTimer.singleShot(0, self._align_title_bar_left)
        QTimer.singleShot(50, self._align_title_bar_left)
        QTimer.singleShot(0, self._adapt_navigation_for_width)
        QTimer.singleShot(0, lambda: self._apply_resolution_adaptive_metrics(force=True))
        self._schedule_splitter_rebalance()
        QTimer.singleShot(1, self._deferred_startup_stage_protocols)
        self._set_status("正在初始化…")

    # ================================================================
    # UI 布局辅助
    # ================================================================

    def _adjust_navigation_menu_position(self) -> None:
        """把左侧三条杠移动到标题栏下方，避免与程序名称重叠。"""
        navigation = getattr(self, "navigationInterface", None)
        panel = getattr(navigation, "panel", None) if navigation is not None else None
        if panel is None:
            return

        top_layout = getattr(panel, "topLayout", None)
        if top_layout is not None:
            top_layout.setContentsMargins(
                4,
                getattr(self, "_current_title_bar_height", _COMPACT_TITLE_BAR_HEIGHT) + 4,
                4,
                0,
            )

    def _adapt_navigation_for_width(self) -> None:
        """Collapse the navigation rail on narrow logical desktops.

        At 125–200% Windows scaling a 1366px display can expose less than
        1100 logical pixels.  Keeping the 190px expanded rail in that case
        needlessly squeezes every content panel and is a frequent source of
        clipped button captions.
        """
        navigation = getattr(self, "navigationInterface", None)
        if navigation is None:
            return
        width = int(self.width())
        auto_collapsed = bool(getattr(self, "_smst_auto_nav_collapsed", False))
        try:
            if width < 1120 and not auto_collapsed:
                navigation.collapse(useAni=False)
                self._smst_auto_nav_collapsed = True
            elif width >= 1320 and auto_collapsed:
                navigation.expand(useAni=False)
                self._smst_auto_nav_collapsed = False
        except TypeError:
            try:
                if width < 1120 and not auto_collapsed:
                    navigation.collapse()
                    self._smst_auto_nav_collapsed = True
                elif width >= 1320 and auto_collapsed:
                    navigation.expand()
                    self._smst_auto_nav_collapsed = False
            except Exception:
                pass
        except Exception:
            pass

    def _schedule_splitter_rebalance(self) -> None:
        timer = getattr(self, "_splitter_rebalance_timer", None)
        if timer is not None:
            timer.start()

    def _deferred_startup_stage_protocols(self) -> None:
        """首屏绘制后再读取协议文件，让窗口尽快出现。"""
        try:
            self._load_protocols()
        finally:
            QTimer.singleShot(1, self._deferred_startup_stage_ports)

    def _deferred_startup_stage_ports(self) -> None:
        """协议列表就绪后再扫描系统串口。"""
        try:
            self._refresh_ports(silent=True)
        finally:
            self._port_watch_timer.start(3000)
            if self._monitor_port:
                QTimer.singleShot(1, self._apply_monitor_args)
            self._startup_ready = True
            self._set_status("就绪")

    def _hide_navigation_back_button(self) -> None:
        """隐藏左侧导航展开后产生的返回箭头，仅调整界面显示。"""
        owners = []
        navigation = getattr(self, "navigationInterface", None)
        title_bar = getattr(self, "titleBar", None)
        for owner in (
            navigation,
            getattr(navigation, "panel", None) if navigation is not None else None,
            getattr(navigation, "navigationPanel", None) if navigation is not None else None,
            title_bar,
        ):
            if owner is not None and owner not in owners:
                owners.append(owner)

        hidden: set[int] = set()
        for owner in owners:
            for attr_name in (
                "returnButton", "returnBtn", "backButton", "backBtn",
                "backPushButton", "returnPushButton",
            ):
                button = getattr(owner, attr_name, None)
                if button is None or id(button) in hidden:
                    continue
                hidden.add(id(button))
                try:
                    button.hide()
                    button.setEnabled(False)
                    button.setFixedSize(0, 0)
                except Exception:
                    pass

            # 不依赖特定 qfluentwidgets 版本：按对象名补充查找返回按钮。
            try:
                for button in owner.findChildren(QAbstractButton):
                    name = str(button.objectName() or "").lower()
                    if "back" not in name and "return" not in name:
                        continue
                    if id(button) in hidden:
                        continue
                    hidden.add(id(button))
                    button.hide()
                    button.setEnabled(False)
                    button.setFixedSize(0, 0)
            except Exception:
                pass

    def _position_shared_toolbar_in_title_bar(self) -> None:
        """把共享功能按钮独立放在标题栏水平中央，不挤动左侧程序名称。"""
        title_bar = getattr(self, "titleBar", None)
        toolbar = getattr(self, "top_bar", None)
        if title_bar is None or toolbar is None or toolbar.parent() is not title_bar:
            return

        try:
            toolbar.adjustSize()
            preferred = toolbar.sizeHint()
            toolbar_width = max(1, preferred.width())
            toolbar_height = getattr(
                self, "_current_title_bar_height", _COMPACT_TITLE_BAR_HEIGHT
            )

            # 左侧给程序图标和名称留出空间，右侧给三个系统窗口按钮留出空间。
            left_reserve = max(220, QFontMetrics(self.font()).horizontalAdvance(self.windowTitle()) + 54)
            right_reserve = 3 * _WINDOW_BUTTON_WIDTH + 12
            maximum_width = max(1, self.width() - left_reserve - right_reserve)
            if toolbar_width > maximum_width:
                return

            x = (self.width() - toolbar_width) // 2
            x = max(left_reserve, min(x, self.width() - right_reserve - toolbar_width))
            toolbar.setMinimumSize(toolbar_width, toolbar_height)
            if toolbar.maximumWidth() < toolbar_width:
                toolbar.setMaximumWidth(16_777_215)
            if toolbar.maximumHeight() < toolbar_height:
                toolbar.setMaximumHeight(16_777_215)
            toolbar.resize(toolbar_width, toolbar_height)
            toolbar.move(x, 0)
            toolbar.raise_()
            toolbar.show()
        except Exception:
            pass

    def _align_title_bar_left(self) -> None:
        """程序名称保持左上角，功能按钮保持标题栏水平居中。"""
        title_bar = getattr(self, "titleBar", None)
        if title_bar is None:
            return

        self._hide_navigation_back_button()

        title_point_size = _responsive_point_size(
            self, base=_TITLE_FONT_POINT_SIZE, maximum=13
        )
        title_bar_height = max(
            _COMPACT_TITLE_BAR_HEIGHT,
            QFontMetrics(_make_crisp_ui_font(title_point_size)).height() + 18,
        )
        self._current_title_bar_height = title_bar_height
        try:
            title_bar.setFixedHeight(title_bar_height)
            title_bar.move(0, 0)
            title_bar.resize(self.width(), title_bar_height)
            title_bar.setContentsMargins(0, 0, 0, 0)
            title_bar.raise_()
            wrapper = getattr(self, "shared_wrapper", None)
            if wrapper is not None and wrapper.layout() is not None:
                wrapper.layout().setContentsMargins(2, title_bar_height + 4, 10, 6)
        except Exception:
            pass

        title_layout = getattr(title_bar, "hBoxLayout", None)
        if title_layout is not None:
            try:
                title_layout.setContentsMargins(4, 0, 0, 0)
                title_layout.setSpacing(4)
                title_layout.setAlignment(Qt.AlignmentFlag.AlignVCenter)
            except Exception:
                pass

        icon_label = getattr(title_bar, "iconLabel", None)
        if icon_label is not None:
            try:
                icon_label.setContentsMargins(0, 0, 0, 0)
                icon_label.setFixedSize(_TITLE_ICON_SIZE, _TITLE_ICON_SIZE)
                icon = self.windowIcon()
                if not icon.isNull():
                    icon_label.setPixmap(icon.pixmap(_TITLE_ICON_SIZE, _TITLE_ICON_SIZE))
                icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                if title_layout is not None:
                    title_layout.setAlignment(icon_label, Qt.AlignmentFlag.AlignVCenter)
            except Exception:
                pass

        title_label = getattr(title_bar, "titleLabel", None)
        if title_label is None:
            # qfluentwidgets versions use different attribute names.  Fall back
            # to the label whose text matches the window title.
            try:
                for candidate in title_bar.findChildren(QLabel):
                    if candidate.text().strip() == self.windowTitle().strip():
                        title_label = candidate
                        break
            except Exception:
                title_label = None

        if title_label is not None:
            try:
                title_label.setContentsMargins(0, 0, 0, 0)
                title_font = _make_crisp_ui_font(title_point_size)
                title_font.setWeight(QFont.Weight.Medium)
                title_label.setFont(title_font)
                title_label.setStyleSheet(
                    f'font-family: "{_UI_FONT_FAMILY}"; '
                    f'font-size: {title_point_size}pt; font-weight: 500;'
                )
                title_label.setMinimumHeight(title_bar_height)
                title_label.setAlignment(
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
                )
                if title_layout is not None:
                    title_layout.setAlignment(title_label, Qt.AlignmentFlag.AlignVCenter)
            except Exception:
                pass

        seen_buttons: set[int] = set()
        title_buttons: list[QAbstractButton] = []
        for attr_name in (
            "minBtn", "maxBtn", "closeBtn",
            "minButton", "maxButton", "closeButton",
            "minimizeButton", "maximizeButton",
        ):
            button = getattr(title_bar, attr_name, None)
            if button is None or id(button) in seen_buttons:
                continue
            seen_buttons.add(id(button))
            title_buttons.append(button)

        if len(title_buttons) < 3:
            try:
                for button in title_bar.findChildren(QAbstractButton):
                    # 共享工具栏现在嵌入标题栏；只把标题栏的直接子按钮视为
                    # 最小化/最大化/关闭按钮，避免误改“发送面板”等工具按钮尺寸。
                    if button.parent() is not title_bar:
                        continue
                    if id(button) in seen_buttons:
                        continue
                    seen_buttons.add(id(button))
                    title_buttons.append(button)
            except Exception:
                pass

        # The last three buttons in Fluent title bars are minimize/maximize/close.
        # Use standard Fluent/Windows logical dimensions.  Qt scales these
        # automatically for the active screen DPI.
        for button in title_buttons[-3:]:
            try:
                button.setContentsMargins(0, 0, 0, 0)
                button.setMinimumSize(_WINDOW_BUTTON_WIDTH, title_bar_height)
                button.setMaximumSize(_WINDOW_BUTTON_WIDTH, title_bar_height)
                button.setFixedSize(_WINDOW_BUTTON_WIDTH, title_bar_height)
                button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                button.setIconSize(QSize(_WINDOW_BUTTON_ICON_SIZE, _WINDOW_BUTTON_ICON_SIZE))
                button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
                if title_layout is not None:
                    title_layout.setAlignment(button, Qt.AlignmentFlag.AlignVCenter)
                # Remove the previous oversized font override while preserving
                # any visual rules supplied by qfluentwidgets.
                existing_qss = button.styleSheet() or ""
                filtered = "\n".join(
                    line for line in existing_qss.splitlines()
                    if "font-size:" not in line
                )
                centering_qss = "\nQAbstractButton { padding: 0px; margin: 0px; }"
                button.setStyleSheet(filtered + centering_qss)
            except Exception:
                pass

        self._update_shared_toolbar_placement()


    def _apply_resolution_adaptive_metrics(self, *, force: bool = False) -> None:
        """Refresh fonts and content-sized controls after a DPI/resolution change."""
        scale_key = round(_effective_ui_scale(self), 3)
        if not force and getattr(self, "_last_ui_scale_key", None) == scale_key:
            return
        self._last_ui_scale_key = scale_key

        body_point_size = apply_application_font(self)
        combo_font = _make_crisp_ui_font(body_point_size)
        for attr_name in (
            "port_combo", "baud_combo", "bytesize_combo", "stopbits_combo",
            "product_combo", "crc_algo_combo",
        ):
            combo = getattr(self, attr_name, None)
            if combo is None:
                continue
            try:
                combo.setFont(combo_font)
                _sync_combo_popup_font(combo)
            except Exception:
                pass

        table = getattr(self, "cmdlib_table", None)
        if table is not None:
            # Keep command-library text at the normal application point size.
            # Qt handles physical DPI scaling; resolution-based multiplication
            # would make the table disproportionately large on 2K/4K screens.
            table_font = _make_crisp_ui_font(body_point_size)
            self._cmdlib_table_font = table_font
            table.setFont(table_font)
            _apply_cmdlib_table_font_style(table, table_font)
            for row in range(table.rowCount()):
                for column in (0, 1):
                    item = table.item(row, column)
                    if item is not None:
                        item.setFont(table_font)
            metrics = QFontMetrics(table_font)
            row_height = max(32, metrics.height() + 10)
            self._cmdlib_row_height = row_height
            table.verticalHeader().setDefaultSectionSize(row_height)
            table.verticalHeader().setMinimumSectionSize(row_height)

            header_font = QFont(table_font)
            header_font.setWeight(QFont.Weight.DemiBold)
            table.horizontalHeader().setFont(header_font)
            table.horizontalHeader().setMinimumHeight(
                max(30, QFontMetrics(header_font).height() + 10)
            )

            send_buttons = getattr(self, "_cmdlib_send_buttons", [])
            widest = 0
            tallest = 0
            for button in send_buttons:
                button.setFont(table_font)
                width, height = _fit_button_to_text(
                    button,
                    horizontal_padding=24,
                    vertical_padding=8,
                    minimum_width=_CMDLIB_SEND_BUTTON_MIN_WIDTH,
                    minimum_height=_CMDLIB_SEND_BUTTON_MIN_HEIGHT,
                )
                button.setMaximumSize(16_777_215, 16_777_215)
                widest = max(widest, width)
                tallest = max(tallest, height)
            if widest:
                table.setColumnWidth(
                    2, max(_CMDLIB_ACTION_COLUMN_WIDTH, widest + 16)
                )
            else:
                table.setColumnWidth(2, _CMDLIB_ACTION_COLUMN_WIDTH)
            if tallest:
                resolved = max(row_height, tallest + 6)
                table.verticalHeader().setDefaultSectionSize(resolved)
                table.verticalHeader().setMinimumSectionSize(resolved)

        action_buttons = tuple(
            button for button in (
                getattr(self, "btn_send_once", None),
                getattr(self, "btn_clear_send", None),
                getattr(self, "btn_crlf", None),
                getattr(self, "btn_crc", None),
                getattr(self, "btn_cycle", None),
            ) if button is not None
        )
        if action_buttons:
            action_font = _make_crisp_ui_font(body_point_size)
            for button in action_buttons:
                button.setFont(action_font)
                _fit_button_to_text(
                    button,
                    horizontal_padding=16,
                    vertical_padding=5,
                    minimum_height=30,
                )
            for widget_name in ("interval_label", "interval_spin", "crc_algo_combo"):
                widget = getattr(self, widget_name, None)
                if widget is not None:
                    widget.setFont(action_font)

        metrics = QFontMetrics(combo_font)
        baud_combo = getattr(self, "baud_combo", None)
        if baud_combo is not None:
            baud_width = max(110, metrics.horizontalAdvance("6000000") + 46)
            baud_combo.setMinimumWidth(baud_width)
            if baud_combo.maximumWidth() < baud_width:
                baud_combo.setMaximumWidth(16_777_215)

        mode_button_height = max(32, metrics.height() + 12)
        for name in ("btn_mode_proto", "btn_mode_hex", "btn_mode_ascii"):
            button = getattr(self, name, None)
            if button is not None:
                button.setMinimumHeight(mode_button_height)
                button.setMaximumHeight(16_777_215)
        send_editor_height = max(72, mode_button_height * 2 + 10)
        for name in ("fields_edit", "raw_edit"):
            editor = getattr(self, name, None)
            if editor is not None:
                editor.setMinimumHeight(send_editor_height)
                editor.setMaximumHeight(120)
                editor.setFont(combo_font)
        mode_container = getattr(self, "send_mode_widget", None)
        if mode_container is not None:
            mode_container.setMinimumHeight(mode_button_height)
            mode_container.setMaximumHeight(16_777_215)
        center_container = getattr(self, "send_center_widget", None)
        if center_container is not None:
            center_container.setMinimumHeight(send_editor_height)
            center_container.setMaximumHeight(120)

        # Recalculate every text-bearing control from its current font metrics.
        # This is intentionally applied after the page-specific adjustments so
        # no fixed/minimum size can leave a caption vertically or horizontally
        # clipped on another Windows DPI setting.
        apply_adaptive_geometry(self, body_point_size)
        self._relayout_send_panel(force=True)
        self._update_shared_toolbar_placement()

        mcu_page = getattr(self, "mcu_page", None)
        if mcu_page is not None:
            try:
                mcu_page.apply_dpi_metrics(body_point_size)
            except Exception:
                pass

        # The two real-time data QTextEdit widgets have an independent user
        # selected size.  Reapply it after QApplication font changes.
        serial_text = getattr(self, "serial_text", None)
        serial_spin = getattr(self, "data_font_spin", None)
        if serial_text is not None and serial_spin is not None:
            try:
                serial_text.set_data_font_point_size(serial_spin.value())
            except Exception:
                pass

        self._align_title_bar_left()

    def _rebalance_main_splitter(self) -> None:
        """稳定页签1下方的“指令库 | 发送面板”分栏。"""
        splitter = getattr(self, "main_splitter", None)
        if splitter is None:
            page = getattr(self, "receive_page", None)
            splitter = getattr(page, "main_splitter", None) if page is not None else None
        if splitter is None or splitter.count() < 2:
            return
        total = max(1, splitter.contentsRect().width() - splitter.handleWidth())
        left_widget = splitter.widget(0)
        right_widget = splitter.widget(1)
        left_min = max(360, left_widget.minimumWidth())
        right_min = max(420, right_widget.minimumSizeHint().width())
        left = max(left_min, total // 2)
        right = max(right_min, total - left)
        if left + right > total:
            # 窗口过窄时仍保持两侧为正数，Qt 会提供必要的内部压缩/滚动。
            right = max(1, total - min(left, max(1, total - 1)))
            left = max(1, total - right)
        splitter.setSizes([left, right])

    def _relayout_all_panels(self) -> None:
        """窗口几何稳定后统一执行全部响应式布局。

        构建期卡片的 ``contentsRect()`` 可能仍为 0，首次显示又不一定触发
        ``resizeEvent``。因此在 showEvent 后延迟重排，避免首屏停留在窄屏形态。
        """
        self._update_shared_toolbar_placement()
        self._relayout_serial_main_row()
        self._relayout_serial_detail_rows()
        self._relayout_receive_toolbars()
        self._relayout_send_panel(force=True)
        self._update_shared_page_scroll_policy()
        self._schedule_splitter_rebalance()

        page = getattr(self, "receive_page", None)
        if page is not None:
            rebalance = getattr(page, "_rebalance_splitter", None)
            if callable(rebalance):
                QTimer.singleShot(0, rebalance)

        mcu = getattr(self, "mcu_page", None)
        if mcu is not None and mcu.isVisible():
            relayout_all = getattr(mcu, "_relayout_all_mcu", None)
            if callable(relayout_all):
                relayout_all()
            else:
                for name in (
                    "_relayout_operation_bar",
                    "_relayout_data_bar",
                    "_relayout_attr_header",
                    "_relayout_autoreply_header",
                ):
                    fn = getattr(mcu, name, None)
                    if callable(fn):
                        fn()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not getattr(self, "_smst_screen_change_connected", False):
            handle = self.windowHandle()
            if handle is not None:
                try:
                    handle.screenChanged.connect(self._on_window_screen_changed)
                    self._smst_screen_change_connected = True
                except Exception:
                    pass
        QTimer.singleShot(0, lambda: self._apply_resolution_adaptive_metrics(force=True))
        # 首次显示保留 0/100ms 双保险；后续置顶切换等 show() 只需一次，
        # 避免同一几何重复进行 4~6 轮全量重排。
        QTimer.singleShot(0, self._relayout_all_panels)
        if not self._first_show_relayout_done:
            self._first_show_relayout_done = True
            QTimer.singleShot(100, self._relayout_all_panels)

    def _on_window_screen_changed(self, screen) -> None:
        del screen
        self._last_ui_scale_key = None
        QTimer.singleShot(0, lambda: self._apply_resolution_adaptive_metrics(force=True))
        QTimer.singleShot(0, self._relayout_all_panels)
        QTimer.singleShot(100, self._relayout_all_panels)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)

        # 保持自定义标题栏贴合窗口；中间页面与左侧导航由 FluentWindow 布局管理。
        if hasattr(self, "titleBar"):
            self._align_title_bar_left()

        timer = getattr(self, "_layout_resize_timer", None)
        if timer is not None:
            timer.start()

    def _apply_debounced_resize_layout(self) -> None:
        self._adjust_navigation_menu_position()
        self._adapt_navigation_for_width()
        self._apply_resolution_adaptive_metrics()
        self._relayout_serial_main_row()
        self._relayout_serial_detail_rows()
        self._relayout_receive_toolbars()
        self._relayout_send_panel()
        self._update_shared_toolbar_placement()
        self._update_shared_page_scroll_policy()
        self._schedule_splitter_rebalance()

    # ================================================================
    # UI 构建
    # ================================================================

    def _build_ui(self) -> None:
        # ---- 共享顶部：工具栏 + 串口配置 ----
        self.top_bar = self._build_top_bar()
        self.serial_config_card = self._build_serial_config_card()

        # ---- 共享底部：发送面板 ----
        self.send_card = self._build_send_card()

        # ---- 重组 FluentWindow：左侧导航保持原位，右侧为共享顶部/页面/共享底部 ----
        wrapper = QWidget(self)
        wrapper.setObjectName("sharedContentWrapper")
        wrapper.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.shared_wrapper = wrapper
        shared_layout = QVBoxLayout(wrapper)
        self.shared_layout = shared_layout
        # FluentWindow 的自定义标题栏位于窗口最上层。共享工具栏如果从 y=0
        # 开始布局，会被标题栏透明区域覆盖，视觉可见但无法接收鼠标点击。
        # 顶部留出标题栏高度后，“添加串口/保存日志/发送面板/置顶”均可正常点击。
        shared_layout.setContentsMargins(
            2, self._current_title_bar_height + 4, 10, 6
        )
        shared_layout.setSpacing(4)
        # 顶部工具按钮优先放入真正的 Fluent 标题栏，使其保持截图中的顶部位置，
        # 同时不再被标题栏透明拖动层遮挡。旧版本接口不兼容时才退回标题栏下方。
        if not self._attach_shared_toolbar_to_title_bar():
            shared_layout.addWidget(
                self.top_bar, 0, Qt.AlignmentFlag.AlignHCenter
            )
        # 串口配置 + 当前功能页 + 发送面板统一放入内容滚动容器。
        # 接收分析页高度不足时只出现垂直滚动条，避免发送面板被截断；
        # 模拟 MCU 页继续使用自身的横/纵向 splitter 自适应，不启用外层滚动。
        self.shared_page_scroll = QScrollArea(wrapper)
        self.shared_page_scroll.setObjectName("sharedReceivePageScroll")
        self.shared_page_scroll.setWidgetResizable(True)
        self.shared_page_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.shared_page_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.shared_page_scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.shared_page_scroll_content = QWidget(self.shared_page_scroll)
        self.shared_page_scroll_content.setObjectName("sharedReceivePageScrollContent")
        self.shared_page_scroll_content.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        self.shared_page_layout = QVBoxLayout(self.shared_page_scroll_content)
        self.shared_page_layout.setContentsMargins(0, 0, 0, 0)
        self.shared_page_layout.setSpacing(4)
        self.shared_page_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.shared_page_layout.addWidget(self.serial_config_card)

        try:
            self.hBoxLayout.removeWidget(self.stackedWidget)
        except Exception:
            pass
        self.shared_page_layout.addWidget(self.stackedWidget, 1)
        self.shared_page_layout.addWidget(self.send_card)
        self.shared_page_scroll.setWidget(self.shared_page_scroll_content)
        shared_layout.addWidget(self.shared_page_scroll, 1)
        # top_bar 构建时 send_card 尚未创建；这里在卡片就绪后同步一次初始状态。
        self.set_send_panel_visible(
            bool(getattr(self, "btn_send_panel", None) is None
                 or self.btn_send_panel.isChecked())
        )

        self.status_bar_widget = self._build_status_bar()
        shared_layout.addWidget(self.status_bar_widget)

        try:
            self.hBoxLayout.addWidget(wrapper, 1)
        except TypeError:
            self.hBoxLayout.addWidget(wrapper)

        # ---- 两个完全隔离的功能页面 ----
        self.receive_page = ReceiveAnalysisPage(self)
        self.realtime_card = self._build_realtime_card()
        self.cmdlib_card = self._build_cmdlib_card()
        self.receive_page.attach(
            self.realtime_card, self.cmdlib_card, self.receive_basic_row
        )
        self._cmdlib_load()

        # 页签2依赖主窗口的协议显示开关，放在页签1控件创建完成后实例化。
        self.mcu_page = McuSimulatePage(self)

        self.addSubInterface(
            self.receive_page, FluentIcon.MESSAGE, "串口接收分析"
        )
        self.addSubInterface(
            self.mcu_page, FluentIcon.SEND, "模拟MCU工具"
        )
        try:
            self.stackedWidget.setCurrentWidget(self.receive_page)
            self.stackedWidget.currentChanged.connect(
                self._update_shared_page_scroll_policy
            )
        except Exception:
            pass
        QTimer.singleShot(0, self._update_shared_page_scroll_policy)

        # 双页签互斥：监控中禁止切换到另一个页签，防止 Word/JSON 协议冲突。
        # 真正的拦截在重写的 switchTo() 中完成（切换发生前拦截，无动画）。
        self._monitoring_page = None  # None=未监控, 0=串口接收分析, 1=模拟MCU工具

        # 左侧导航需要显示名称；不同 qfluentwidgets 版本接口可能不同。
        try:
            self.navigationInterface.setExpandWidth(190)
            self.navigationInterface.expand(useAni=False)
            self._adjust_navigation_menu_position()
            QTimer.singleShot(0, self._adjust_navigation_menu_position)
            QTimer.singleShot(80, self._adjust_navigation_menu_position)
            QTimer.singleShot(0, self._adapt_navigation_for_width)
        except Exception:
            pass
        # 展开左侧导航后，某些 qfluentwidgets 版本会自动显示返回箭头。
        # 该按钮在本工具中没有用途，延迟隐藏以覆盖导航内部的异步刷新。
        self._hide_navigation_back_button()
        QTimer.singleShot(0, self._hide_navigation_back_button)
        QTimer.singleShot(80, self._hide_navigation_back_button)

        # 旧的单页右侧懒加载容器废弃。
        self.main_splitter = None
        self.right_stacked = None
        self.attr_panel_card = None
        self.preset_panel_card = None

    def _attach_shared_toolbar_to_title_bar(self) -> bool:
        """Place the shared toolbar in the title bar when enough width exists."""
        title_bar = getattr(self, "titleBar", None)
        title_layout = getattr(title_bar, "hBoxLayout", None) if title_bar else None
        toolbar = getattr(self, "top_bar", None)
        if title_bar is None or toolbar is None:
            return False
        try:
            shared_layout = getattr(self, "shared_layout", None)
            if shared_layout is not None:
                shared_layout.removeWidget(toolbar)
            if title_layout is not None:
                title_layout.removeWidget(toolbar)
            toolbar.setParent(title_bar)
            toolbar.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)
            toolbar.setMaximumWidth(16_777_215)
            toolbar.setMaximumHeight(16_777_215)
            self._toolbar_location = "title"
            self._relayout_top_bar(in_title_bar=True)
            toolbar.show()
            self._position_shared_toolbar_in_title_bar()
            return True
        except Exception:
            return False

    def _move_shared_toolbar_below_title_bar(self) -> bool:
        """Move the toolbar into the normal layout instead of compressing text."""
        toolbar = getattr(self, "top_bar", None)
        shared_layout = getattr(self, "shared_layout", None)
        wrapper = getattr(self, "shared_wrapper", None)
        if toolbar is None or shared_layout is None or wrapper is None:
            return False
        try:
            title_bar = getattr(self, "titleBar", None)
            title_layout = getattr(title_bar, "hBoxLayout", None) if title_bar else None
            if title_layout is not None:
                title_layout.removeWidget(toolbar)
            toolbar.setParent(wrapper)
            toolbar.setMinimumWidth(0)
            toolbar.setMaximumWidth(16_777_215)
            toolbar.setMaximumHeight(16_777_215)
            toolbar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            shared_layout.insertWidget(0, toolbar)
            self._toolbar_location = "content"
            self._relayout_top_bar(in_title_bar=False)
            toolbar.show()
            toolbar.updateGeometry()
            return True
        except Exception:
            return False

    def _update_shared_toolbar_placement(self) -> None:
        """Use title-bar placement only when the full button row actually fits."""
        toolbar = getattr(self, "top_bar", None)
        if toolbar is None:
            return
        try:
            self._relayout_top_bar(in_title_bar=True)
            toolbar.adjustSize()
            preferred_width = max(1, int(toolbar.sizeHint().width()))
        except Exception:
            preferred_width = 480
        left_reserve = max(220, QFontMetrics(self.font()).horizontalAdvance(self.windowTitle()) + 54)
        right_reserve = 3 * _WINDOW_BUTTON_WIDTH + 12
        usable = max(0, int(self.width()) - left_reserve - right_reserve - 20)
        should_use_title = usable >= preferred_width and self.width() >= 900
        location = getattr(self, "_toolbar_location", "")
        if should_use_title:
            if location != "title":
                self._attach_shared_toolbar_to_title_bar()
            else:
                self._relayout_top_bar(in_title_bar=True)
                self._position_shared_toolbar_in_title_bar()
        else:
            if location != "content":
                self._move_shared_toolbar_below_title_bar()
            else:
                self._relayout_top_bar(in_title_bar=False)

    def set_send_panel_visible(self, visible: bool) -> None:
        """控制两个导航页面共用的底部发送面板。"""
        visible = bool(visible)
        card = getattr(self, "send_card", None)
        if card is not None:
            # setHidden 比依赖父容器当前可见性的 isVisible/setVisible 组合更稳定。
            card.setHidden(not visible)
            card.updateGeometry()

        # 允许其他代码调用本方法时同步顶部开关，但阻止信号递归。
        button = getattr(self, "btn_send_panel", None)
        if button is not None and button.isChecked() != visible:
            old = button.blockSignals(True)
            try:
                button.setChecked(visible)
            finally:
                button.blockSignals(old)

        wrapper = getattr(self, "shared_wrapper", None)
        if wrapper is not None and wrapper.layout() is not None:
            wrapper.layout().invalidate()
            wrapper.layout().activate()
        self.updateGeometry()
        QTimer.singleShot(0, self._update_shared_page_scroll_policy)

    def _update_shared_page_scroll_policy(self, *_args) -> None:
        """接收页使用垂直滚动兜底，模拟 MCU 页保持自身 splitter 自适应。"""
        scroll = getattr(self, "shared_page_scroll", None)
        content = getattr(self, "shared_page_scroll_content", None)
        layout = getattr(self, "shared_page_layout", None)
        stacked = getattr(self, "stackedWidget", None)
        receive_page = getattr(self, "receive_page", None)
        if scroll is None or content is None or layout is None or stacked is None:
            return

        receive_active = receive_page is not None and stacked.currentWidget() is receive_page
        if receive_active:
            layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            layout.invalidate()
            layout.activate()
            required_height = max(
                int(layout.minimumSize().height()),
                int(layout.sizeHint().height()),
            )
            content.setMinimumHeight(required_height)
        else:
            layout.setSizeConstraint(QLayout.SizeConstraint.SetDefaultConstraint)
            content.setMinimumHeight(0)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content.updateGeometry()
        scroll.updateGeometry()

    def _on_shared_send_panel_clicked(self, checked: bool = False) -> None:
        """顶部共享按钮的直接回调，两个导航页面中均可使用。"""
        button = getattr(self, "btn_send_panel", None)
        visible = bool(button.isChecked()) if button is not None else bool(checked)
        self.set_send_panel_visible(visible)


    def get_collector(self):
        return self.collector

    def get_cfg(self):
        return self.cfg

    def get_attr_center(self):
        return self._attr_center

    def get_cmd_engine(self):
        return self._cmd_engine

    def get_auto_reply(self):
        return self._auto_reply

    def _is_mcu_auto_reply_context_active(self) -> bool:
        """Only allow automatic replies for a monitor session started on MCU page.

        This method is called from the serial worker thread, so it intentionally
        reads only plain Python state and never accesses Qt widgets.
        """
        if self.mcu_page is None or self._monitoring_page != 1:
            return False
        selected = str(getattr(self, "product_var", "") or "").strip()
        if not selected or getattr(self, "_product_kinds", {}).get(selected) != "json":
            return False
        return (
            str((self._mcu_cfg or {}).get("import_source") or "").strip().lower() == "json"
            and str((self._attr_center.cfg or {}).get("import_source") or "").strip().lower() == "json"
        )


    def switchTo(self, interface):
        """双页签互斥：监控中禁止切换页签。

        重写 qfluentwidgets 的 switchTo，在页面真正切换之前拦截，
        因此不会触发任何切换动画，只弹出提示。
        """
        if self.is_collecting and self._monitoring_page is not None:
            locked = self.receive_page if self._monitoring_page == 0 else self.mcu_page
            if interface is not locked:
                # 导航项点击时 qfluentwidgets 已先把高亮移到被点击项，
                # 这里瞬时把高亮移回锁定页签（临时关闭指示条动画，避免抖动），
                # 并停止/隐藏可能正在播放的指示条动画。
                try:
                    nav = self.navigationInterface
                    was_ani = nav.isIndicatorAnimationEnabled()
                    nav.setIndicatorAnimationEnabled(False)
                    nav.setCurrentItem(locked.objectName())
                    panel = getattr(nav, "panel", None)
                    if panel is not None and hasattr(panel, "_stopIndicatorAnimation"):
                        panel._stopIndicatorAnimation()
                    nav.setIndicatorAnimationEnabled(was_ani)
                except Exception:
                    pass
                page_name = "串口接收分析" if self._monitoring_page == 0 else "模拟MCU工具"
                QMessageBox.warning(
                    self, "页签已锁定",
                    f"正在监控中（{page_name} 页签），\n请先停止监控再切换到另一个页签。"
                )
                return
        super().switchTo(interface)
        QTimer.singleShot(0, self._update_shared_page_scroll_policy)

    def get_protocol_dir(self) -> Path:
        return get_protocol_dir()

    def _sync_collector_cfg(self) -> None:
        """同步页面1使用的主协议解析配置。"""
        collector = self.collector
        if collector is None:
            return
        try:
            collector.cfg = self.cfg or {}
            if getattr(collector, "sync", None) is not None:
                collector.sync.cfg = collector.cfg
        except Exception:
            pass

    def _sync_mcu_collector_cfg(self) -> None:
        """同步模拟 MCU 独立 HEX 解析配置，不改变页面1显示模式。"""
        collector = self.collector
        if collector is None:
            return
        try:
            setter = getattr(collector, "set_mcu_cfg", None)
            if callable(setter):
                setter(self._mcu_cfg)
            else:
                collector.mcu_cfg = self._mcu_cfg
        except Exception:
            pass

    def _build_top_bar(self) -> QWidget:
        bar = QWidget()
        self.top_bar_layout = QGridLayout(bar)
        self.top_bar_layout.setContentsMargins(12, 0, 12, 0)
        self.top_bar_layout.setHorizontalSpacing(8)
        self.top_bar_layout.setVerticalSpacing(6)

        self.btn_add_port = PushButton("添加串口")
        self.btn_add_port.clicked.connect(self._safe(self._add_serial_port))

        self.btn_save_log = PushButton("保存日志")
        self.btn_save_log.clicked.connect(self._safe(self._choose_log))

        self.btn_send_panel = ToggleButton("发送面板")
        self.btn_send_panel.setCheckable(True)
        self.btn_send_panel.setChecked(False)
        self.btn_send_panel.clicked.connect(self._on_shared_send_panel_clicked)

        self.btn_topmost = ToggleButton("置顶")
        self.btn_topmost.toggled.connect(self._safe(self._on_topmost_toggled))

        self._top_bar_buttons = (
            self.btn_add_port,
            self.btn_save_log,
            self.btn_send_panel,
            self.btn_topmost,
        )
        for button in self._top_bar_buttons:
            fit_text_control(button)
        self._relayout_top_bar(in_title_bar=True)
        return bar

    def _relayout_top_bar(self, *, in_title_bar: bool | None = None) -> None:
        layout = getattr(self, "top_bar_layout", None)
        buttons = getattr(self, "_top_bar_buttons", ())
        if layout is None or not buttons:
            return
        if in_title_bar is None:
            in_title_bar = getattr(self, "_toolbar_location", "title") == "title"
        for button in buttons:
            layout.removeWidget(button)
        self._reset_grid_stretches(layout, 8)
        available = int(getattr(self, "top_bar", self).width())
        two_rows = not in_title_bar and 0 < available < 560
        if two_rows:
            for index, button in enumerate(buttons):
                layout.addWidget(button, index // 2, index % 2)
                layout.setColumnStretch(index % 2, 1)
        else:
            for index, button in enumerate(buttons):
                layout.addWidget(button, 0, index)
                layout.setColumnStretch(index, 0)
        layout.invalidate()

    def _build_serial_config_card(self) -> CardWidget:
        card = CardWidget()
        self.serial_config_card = card
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        self.serial_main_grid = QGridLayout()
        self.serial_main_grid.setContentsMargins(0, 0, 0, 0)
        self.serial_main_grid.setHorizontalSpacing(8)
        self.serial_main_grid.setVerticalSpacing(6)

        self.serial_port_label = BodyLabel("串口：")
        self.port_combo = DpiAwareComboBox()
        # 串口名通常较长，但不应为了一个下拉框把整行挤成三层。
        # 180 logical px 足够显示常见 COM 描述，完整内容仍可在下拉列表
        # 和 tooltip 中查看；剩余空间由布局的 stretch 列分配。
        self.port_combo.setMinimumWidth(180)
        self.port_combo.setMaximumWidth(16_777_215)
        self.port_combo.currentTextChanged.connect(self._safe(self._on_port_changed))

        self.btn_refresh_ports = PushButton("刷新")
        self.btn_refresh_ports.clicked.connect(self._safe(lambda checked=False: self._refresh_ports()))

        self.serial_baud_label = BodyLabel("波特率：")
        self.baud_combo = ToggleCloseEditableComboBox()
        self.baud_combo.addItems([
            "9600", "19200", "38400", "57600", "115200", "230400",
            "460800", "921600", "1000000", "1500000", "2000000",
            "3000000", "4000000", "5000000", "6000000"
        ])
        self.baud_combo.setCurrentText("9600")
        # EditableComboBox 的 currentTextChanged 会在每个按键后触发。键入
        # 115200 时绝不能依次用 1/11/115 等临时值重启串口；这里只记录
        # 文本，回车、失焦或从下拉列表确认后才提交。
        self.baud_combo.currentTextChanged.connect(
            self._safe(self._on_baud_text_changed)
        )
        # qfluentwidgets.EditableComboBox 本身继承 LineEdit，并不是
        # QComboBox + 内部 QLineEdit 的组合，因此没有 lineEdit() 方法。
        # 直接连接控件自身的提交信号，兼容当前 PySide6-Fluent-Widgets。
        self.baud_combo.returnPressed.connect(self._safe(self._commit_baud))
        self.baud_combo.editingFinished.connect(self._safe(self._commit_baud))
        try:
            self.baud_combo.activated.connect(
                self._safe(lambda *args: self._commit_baud())
            )
        except Exception:
            pass
        self.baud_combo.setMinimumWidth(112)
        self.baud_combo.setMaximumWidth(168)
        self.baud_combo.setSizePolicy(
            QSizePolicy.Policy.Minimum,
            QSizePolicy.Policy.Fixed,
        )

        self.btn_more_config = ToggleButton("更多 ▼")
        self.btn_more_config.setChecked(False)
        self.btn_more_config.toggled.connect(self._safe(self._on_more_config_toggled))

        self.btn_start = PrimaryPushButton("● 开始监控")
        self.btn_start.clicked.connect(self._safe(self._toggle_serial))
        apply_tooltip(self.btn_start, "开始/停止监控（F5 / Shift+F5）")

        # 操作按钮保持自然宽度，不参与横向拉伸。旧布局把“开始监控”
        # 放在 stretch 列中，窄屏时会形成一整条蓝色大按钮，视觉非常突兀。
        for button in (
            self.btn_refresh_ports, self.btn_more_config, self.btn_start,
        ):
            button.setSizePolicy(
                QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
            )
            fit_text_control(button)

        self._serial_main_widgets = (
            self.serial_port_label, self.port_combo, self.btn_refresh_ports,
            self.serial_baud_label, self.baud_combo, self.btn_more_config,
            self.btn_start,
        )
        self._relayout_serial_main_row()
        layout.addLayout(self.serial_main_grid)

        # 详情区域默认隐藏；展开后显示数据位、停止位及全部存储数据控件。
        self.serial_detail_panel = QFrame(card)
        self.serial_detail_panel.setObjectName("serialDetailPanel")
        self.serial_detail_panel.setStyleSheet(
            f"""
            QFrame#serialDetailPanel {{
                background-color: {PALETTE['surface']};
                border: 1px solid {PALETTE['card_border']};
                border-radius: 8px;
            }}
            """
        )
        detail_layout = QVBoxLayout(self.serial_detail_panel)
        detail_layout.setContentsMargins(10, 8, 10, 8)
        detail_layout.setSpacing(6)

        # 详情区域使用网格而不是两个固定单行。窄屏/高 DPI 下文件名、
        # 路径和操作按钮会自动换到下一行，不再互相挤压。
        self.serial_detail_grid = QGridLayout()
        self.serial_detail_grid.setContentsMargins(0, 0, 0, 0)
        self.serial_detail_grid.setHorizontalSpacing(8)
        self.serial_detail_grid.setVerticalSpacing(6)

        self.bytesize_label = BodyLabel("数据位：")
        self.bytesize_combo = DpiAwareComboBox()
        self.bytesize_combo.addItems(["5", "6", "7", "8"])
        self.bytesize_combo.setCurrentText("8")
        self.bytesize_combo.setMinimumWidth(72)
        self.bytesize_combo.setMaximumWidth(96)

        self.stopbits_label = BodyLabel("停止位：")
        self.stopbits_combo = DpiAwareComboBox()
        self.stopbits_combo.addItems(["1", "1.5", "2"])
        self.stopbits_combo.setCurrentText("1")
        self.stopbits_combo.setMinimumWidth(72)
        self.stopbits_combo.setMaximumWidth(96)

        self.filename_label = BodyLabel("文件名：")
        self.save_name_edit = LineEdit()
        self.save_name_edit.setText(self.save_raw_filename)
        self.save_name_edit.setMinimumWidth(180)
        self.save_name_edit.setMaximumWidth(16_777_215)

        self.btn_save_raw = PushButton("开始存储数据")
        self._save_raw_default_qss = self.btn_save_raw.styleSheet()
        self.btn_save_raw.setMinimumWidth(132)
        self.btn_save_raw.clicked.connect(self._safe(self._toggle_save_raw))

        self.path_label = BodyLabel("路径：")
        self.save_path_edit = LineEdit()
        self.save_path_edit.setText(self.save_raw_path)
        self.save_path_edit.setReadOnly(True)

        self.btn_choose_path = PushButton("选择")
        self.btn_choose_path.clicked.connect(self._safe(self._choose_save_raw_path))
        self.btn_open_receive_location = PushButton("打开文件位置")
        self.btn_open_receive_location.clicked.connect(
            self._safe(self._open_receive_file_location)
        )
        for button in (
            self.btn_save_raw,
            self.btn_choose_path,
            self.btn_open_receive_location,
        ):
            button.setSizePolicy(
                QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
            )
            fit_text_control(button)
        self._serial_detail_widgets = (
            self.bytesize_label, self.bytesize_combo,
            self.stopbits_label, self.stopbits_combo,
            self.filename_label, self.save_name_edit,
            self.btn_save_raw, self.path_label, self.save_path_edit,
            self.btn_choose_path, self.btn_open_receive_location,
        )
        self._relayout_serial_detail_rows()
        detail_layout.addLayout(self.serial_detail_grid)
        layout.addWidget(self.serial_detail_panel)
        self.serial_detail_panel.setVisible(False)
        return card

    def _clear_grid_widgets(self, layout: QGridLayout, widgets) -> None:
        for widget in widgets:
            layout.removeWidget(widget)

    @staticmethod
    def _reset_grid_stretches(layout: QGridLayout, count: int = 12) -> None:
        """清理上一次响应式布局遗留的 stretch。

        QGridLayout 在 removeWidget() 后不会清除列拉伸系数。窗口从宽屏切到
        窄屏再切回来时，旧 stretch 可能落到“选择”“开始监控”等按钮所在列，
        于是按钮被拉成整行。每次重排前必须显式归零。
        """
        for index in range(max(1, int(count))):
            layout.setColumnStretch(index, 0)
            layout.setRowStretch(index, 0)

    def _relayout_serial_main_row(self) -> None:
        layout = getattr(self, "serial_main_grid", None)
        widgets = getattr(self, "_serial_main_widgets", ())
        if layout is None or not widgets:
            return
        self._clear_grid_widgets(layout, widgets)
        self._reset_grid_stretches(layout)
        card = getattr(self, "serial_config_card", self)
        width = int(card.contentsRect().width())
        if width <= 1:
            # 构建期 contentsRect 可能为 0；使用窗口有效宽度近似，避免首屏误入窄档。
            width = max(1, int(self.width()) - 240)
        # 760 logical px 已能容纳紧凑单行。Qt 自身负责 DPI 映射。
        if width >= 760:
            positions = (
                (self.serial_port_label, 0, 0, 1, 1),
                (self.port_combo, 0, 1, 1, 1),
                (self.btn_refresh_ports, 0, 2, 1, 1),
                (self.serial_baud_label, 0, 3, 1, 1),
                (self.baud_combo, 0, 4, 1, 1),
                (self.btn_more_config, 0, 5, 1, 1),
                (self.btn_start, 0, 6, 1, 1),
            )
            layout.setColumnStretch(1, 1)
        elif width >= 540:
            positions = (
                (self.serial_port_label, 0, 0, 1, 1),
                (self.port_combo, 0, 1, 1, 3),
                (self.btn_refresh_ports, 0, 4, 1, 1),
                (self.serial_baud_label, 1, 0, 1, 1),
                (self.baud_combo, 1, 1, 1, 1),
                (self.btn_more_config, 1, 2, 1, 1),
                (self.btn_start, 1, 4, 1, 1),
            )
            layout.setColumnStretch(3, 1)
        else:
            positions = (
                (self.serial_port_label, 0, 0, 1, 1),
                (self.port_combo, 0, 1, 1, 3),
                (self.btn_refresh_ports, 1, 0, 1, 1),
                (self.serial_baud_label, 1, 1, 1, 1),
                (self.baud_combo, 1, 2, 1, 1),
                (self.btn_more_config, 1, 3, 1, 1),
                (self.btn_start, 2, 3, 1, 1),
            )
            layout.setColumnStretch(1, 1)
        for widget, row, column, row_span, column_span in positions:
            layout.addWidget(widget, row, column, row_span, column_span)
        layout.setAlignment(
            self.btn_start,
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
        )
        layout.invalidate()

    def _relayout_serial_detail_rows(self) -> None:
        layout = getattr(self, "serial_detail_grid", None)
        widgets = getattr(self, "_serial_detail_widgets", ())
        if layout is None or not widgets:
            return
        self._clear_grid_widgets(layout, widgets)
        self._reset_grid_stretches(layout)
        card = getattr(self, "serial_config_card", self)
        width = int(card.contentsRect().width())
        if width <= 1:
            # 构建期使用窗口有效宽度，避免首次显示错误地装配为多行。
            width = max(1, int(self.width()) - 240)
        if width >= 760:
            # 两行紧凑表单：第一行串口格式+文件名，第二行保存路径。
            # 只有编辑框所在列可拉伸，所有操作按钮保持自然宽度。
            positions = (
                (self.bytesize_label, 0, 0, 1, 1),
                (self.bytesize_combo, 0, 1, 1, 1),
                (self.stopbits_label, 0, 2, 1, 1),
                (self.stopbits_combo, 0, 3, 1, 1),
                (self.filename_label, 0, 4, 1, 1),
                (self.save_name_edit, 0, 5, 1, 1),
                (self.btn_save_raw, 0, 6, 1, 2),
                (self.path_label, 1, 0, 1, 1),
                (self.save_path_edit, 1, 1, 1, 5),
                (self.btn_choose_path, 1, 6, 1, 1),
                (self.btn_open_receive_location, 1, 7, 1, 1),
            )
            layout.setColumnStretch(5, 1)
        elif width >= 540:
            positions = (
                (self.bytesize_label, 0, 0, 1, 1),
                (self.bytesize_combo, 0, 1, 1, 1),
                (self.stopbits_label, 0, 2, 1, 1),
                (self.stopbits_combo, 0, 3, 1, 1),
                (self.filename_label, 1, 0, 1, 1),
                (self.save_name_edit, 1, 1, 1, 3),
                (self.btn_save_raw, 1, 4, 1, 1),
                (self.path_label, 2, 0, 1, 1),
                (self.save_path_edit, 2, 1, 1, 3),
                (self.btn_choose_path, 2, 4, 1, 1),
                (self.btn_open_receive_location, 3, 4, 1, 1),
            )
            layout.setColumnStretch(3, 1)
        else:
            positions = (
                (self.bytesize_label, 0, 0, 1, 1),
                (self.bytesize_combo, 0, 1, 1, 1),
                (self.stopbits_label, 0, 2, 1, 1),
                (self.stopbits_combo, 0, 3, 1, 1),
                (self.filename_label, 1, 0, 1, 1),
                (self.save_name_edit, 1, 1, 1, 3),
                (self.path_label, 2, 0, 1, 1),
                (self.save_path_edit, 2, 1, 1, 3),
                (self.btn_save_raw, 3, 0, 1, 1),
                (self.btn_choose_path, 3, 2, 1, 1),
                (self.btn_open_receive_location, 3, 3, 1, 1),
            )
            layout.setColumnStretch(1, 1)
        for widget, row, column, row_span, column_span in positions:
            layout.addWidget(widget, row, column, row_span, column_span)
        for button in (
            self.btn_save_raw,
            self.btn_choose_path,
            self.btn_open_receive_location,
        ):
            layout.setAlignment(
                button,
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            )
        layout.invalidate()

    def _relayout_receive_toolbars(self) -> None:
        """Switch long receive-page toolbars to a vertical layout when narrow."""
        receive_page = getattr(self, "receive_page", None)
        card = getattr(receive_page, "_realtime_card", None)
        width = int(card.width()) if card is not None else int(self.width())
        basic = getattr(self, "receive_basic_toolbar", None)
        protocol = getattr(self, "receive_protocol_toolbar", None)
        if basic is not None:
            # 基础接收按钮位于页面标题栏右侧，保持单行紧凑排列。
            basic.setDirection(QBoxLayout.Direction.LeftToRight)
        if protocol is not None:
            protocol.setDirection(
                QBoxLayout.Direction.TopToBottom
                if width < 680 else QBoxLayout.Direction.LeftToRight
            )
        if card is not None:
            apply_adaptive_geometry(card, _UI_FONT_POINT_SIZE)
            card.updateGeometry()

    def _build_realtime_card(self) -> CardWidget:
        card = CardWidget()
        layout = QVBoxLayout(card)
        # 与顶部工具栏、串口配置内容保持同一左/右边线。
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        # 工具条改为上下两行，避免右侧指令库拉宽时与协议控件互相挤压。
        toolbar_widget = QWidget(card)
        toolbar_box = QVBoxLayout(toolbar_widget)
        toolbar_box.setContentsMargins(0, 0, 0, 0)
        toolbar_box.setSpacing(6)

        # 第一行：永久去掉“实时数据”标题。
        basic_row = QWidget(toolbar_widget)
        basic_toolbar = QHBoxLayout(basic_row)
        self.receive_basic_toolbar = basic_toolbar
        self.receive_basic_row = basic_row
        basic_toolbar.setContentsMargins(0, 0, 0, 0)
        basic_toolbar.setSpacing(8)

        # 基础工具按钮保持靠左排列，不随窗口宽度居中移动。
        self.btn_hex = ToggleButton("HEX格式")
        self.btn_hex.setChecked(False)
        apply_tooltip(self.btn_hex, "蓝色：HEX格式；白色：ASCII格式")
        self.btn_hex.toggled.connect(self._safe(self._on_hex_toggled))
        basic_toolbar.addWidget(self.btn_hex)

        self.btn_view_mode = ToggleButton("原始数据模式")
        self.btn_view_mode.setChecked(False)
        self.btn_view_mode.setEnabled(False)
        self.btn_view_mode.toggled.connect(self._safe(self._on_view_mode_toggled))
        basic_toolbar.addWidget(self.btn_view_mode)

        # 保留字号状态控件与全部调节逻辑，但不在工具栏中显示，
        # 避免占用实时数据区域的横向空间。
        self.realtime_font_label = BodyLabel("字号：")
        self.realtime_font_spin = SpinBox()
        self.realtime_font_spin.setRange(
            CtrlWheelZoomTextEdit._FONT_MIN_PT,
            CtrlWheelZoomTextEdit._FONT_MAX_PT,
        )
        self.realtime_font_spin.setValue(_UI_FONT_POINT_SIZE)
        self.realtime_font_spin.setSuffix(" pt")
        # qfluentwidgets 的 SpinBox 左右步进按钮会占用较多宽度。
        # 旧值 92 在高 DPI/不同分辨率下会把数值编辑区挤到几乎不可见，
        # 只剩上下箭头。这里为“24 pt”预留稳定的文本区域。
        self.realtime_font_spin.setMinimumWidth(132)
        self.realtime_font_spin.setMaximumWidth(148)
        self.realtime_font_spin.lineEdit().setMinimumWidth(48)
        self.realtime_font_spin.lineEdit().setAlignment(
            Qt.AlignmentFlag.AlignCenter
        )
        self.realtime_font_spin.setToolTip("仅调整当前实时数据框中的字体大小")
        self.realtime_font_label.hide()
        self.realtime_font_spin.hide()

        self.btn_clear = PushButton("清空")
        self.btn_clear.clicked.connect(self._safe(self._clear_output))
        basic_toolbar.addWidget(self.btn_clear)

        self.btn_autoscroll = ToggleButton("自动滚动")
        self.btn_autoscroll.setChecked(True)
        self.btn_autoscroll.toggled.connect(lambda c: setattr(self, "autoscroll", c))
        basic_toolbar.addWidget(self.btn_autoscroll)
        # 该按钮组稍后由 ReceiveAnalysisPage 放到“串口接收分析”标题右侧。
        # 不在实时数据卡片内部重复占一行。

        # 协议解析控件仍保留在实时数据卡片内，并随解析模式显示/隐藏。
        self.protocol_controls_container = QWidget(toolbar_widget)
        protocol_controls_layout = QHBoxLayout(self.protocol_controls_container)
        self.receive_protocol_toolbar = protocol_controls_layout
        protocol_controls_layout.setContentsMargins(0, 0, 0, 0)
        protocol_controls_layout.setSpacing(8)

        # 协议控件保持靠左排列，右侧弹性空间吸收窗口宽度变化。
        # 模组发送 / MCU发送改为一个整体的分段滑块，只改变界面形态，业务方向不变。
        self.sender_switch = TwoOptionSegmentSwitch(
            "模组发送", "MCU发送", self.protocol_controls_container,
            value=self.serial_sender,
        )
        self.sender_switch.valueChanged.connect(self._safe(self._on_sender_changed))
        self.sender_switch.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        protocol_controls_layout.addWidget(self.sender_switch)

        protocol_controls_layout.addWidget(BodyLabel("产品协议："))
        self.product_combo = DpiAwareComboBox()
        self.product_combo.setMinimumWidth(160)
        self.product_combo.setMaximumWidth(16_777_215)
        self.product_combo.currentTextChanged.connect(self._safe(self._on_product_change))
        protocol_controls_layout.addWidget(self.product_combo)

        self.btn_import = PushButton("导入Word协议")
        self.btn_import.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )
        self.btn_import.clicked.connect(self._safe(self._import_docx))
        protocol_controls_layout.addWidget(self.btn_import)

        self.btn_edit_proto = PushButton("修改/删除")
        self.btn_edit_proto.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )
        self.btn_edit_proto.clicked.connect(self._safe(self._edit_or_delete_word_protocol))
        protocol_controls_layout.addWidget(self.btn_edit_proto)

        self.btn_view_proto = PushButton("查看协议")
        self.btn_view_proto.setSizePolicy(
            QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed
        )
        self.btn_view_proto.clicked.connect(self._safe(self._show_protocol))
        protocol_controls_layout.addWidget(self.btn_view_proto)
        protocol_controls_layout.addStretch(1)

        toolbar_box.addWidget(self.protocol_controls_container)
        self.protocol_controls_container.setVisible(self.btn_view_mode.isChecked())
        layout.addWidget(toolbar_widget)

        # 文本区
        self.serial_text = CtrlWheelZoomTextEdit()
        self.serial_text.setObjectName("RealtimeDataText")
        self.serial_text.setStyleSheet(_TEXT_EDIT_FRAME_QSS)
        self.serial_text.setReadOnly(True)
        self.serial_text.setUndoRedoEnabled(False)
        self.serial_text.setAcceptRichText(False)
        self.serial_text.document().setMaximumBlockCount(self.max_display_lines)
        # 与主界面使用同一套清晰字体，避免不同字体回退/缩放造成发虚。
        self.serial_text.setFont(_make_crisp_ui_font(_UI_FONT_POINT_SIZE))
        self.serial_text.set_data_font_point_size(self.realtime_font_spin.value())
        self.realtime_font_spin.valueChanged.connect(
            self.serial_text.set_data_font_point_size
        )
        self.serial_text.setLineWrapMode(TextEdit.WidgetWidth)
        self.serial_text.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(self.serial_text, stretch=1)

        return card

    def _build_cmdlib_card(self) -> CardWidget:
        card = CardWidget()
        self._cmdlib_min_width = max(
            500,
            round(500 * min(1.20, _effective_ui_scale(card))),
        )
        card.setMinimumWidth(self._cmdlib_min_width)
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(8)
        # 指令库第一行按钮作为一组始终水平居中。
        bar.addStretch(1)
        self.btn_cmdlib_mode = ToggleButton("HEX")
        self.btn_cmdlib_mode.setChecked(True)
        apply_tooltip(self.btn_cmdlib_mode, "蓝色：HEX指令；白色：ASCII指令")
        self.btn_cmdlib_mode.toggled.connect(self._safe(self._cmdlib_toggle_mode))
        bar.addWidget(self.btn_cmdlib_mode)

        self.btn_cmdlib_cycle = PushButton("循环发送")
        self.btn_cmdlib_cycle.clicked.connect(self._safe(self._cmdlib_toggle_cycle))
        bar.addWidget(self.btn_cmdlib_cycle)

        self.btn_cmdlib_cycle_config = PushButton("循环配置")
        self.btn_cmdlib_cycle_config.clicked.connect(
            self._safe(self._cmdlib_open_cycle_config)
        )
        bar.addWidget(self.btn_cmdlib_cycle_config)

        self.btn_cmdlib_crlf = ToggleButton("加回车换行")
        self.btn_cmdlib_crlf.setChecked(bool(self.tx_append_crlf))
        self.btn_cmdlib_crlf.toggled.connect(
            self._safe(self._set_tx_append_crlf)
        )
        apply_tooltip(
            self.btn_cmdlib_crlf,
            "与发送面板的“加回车换行”共用同一状态",
        )
        bar.addWidget(self.btn_cmdlib_crlf)

        # 指令通过下方固定的 40 个空白行直接录入，不再使用“新增”按钮。
        bar.addStretch(1)
        layout.addLayout(bar)

        self.cmdlib_table = TableWidget()
        self.cmdlib_table.setObjectName("CommandLibraryTable")
        # 指令库正文、表头和行按钮与主界面使用同一正常字号；顶部
        # 三个工具按钮保持原样。Qt 点值字体会自动适配系统 DPI。
        # Use the same normal point size as the main interface.  Qt scales
        # point-size fonts for the monitor DPI; do not multiply by resolution.
        self._cmdlib_table_font = _make_crisp_ui_font(_CMDLIB_FONT_BASE_SIZE)
        self.cmdlib_table.setFont(self._cmdlib_table_font)
        _apply_cmdlib_table_font_style(self.cmdlib_table, self._cmdlib_table_font)
        self.cmdlib_table.setColumnCount(3)
        self.cmdlib_table.setHorizontalHeaderLabels(["名称", "指令数据", "操作"])
        self.cmdlib_table.verticalHeader().setVisible(False)
        table_metrics = QFontMetrics(self._cmdlib_table_font)
        self._cmdlib_row_height = max(32, table_metrics.height() + 10)
        self.cmdlib_table.verticalHeader().setDefaultSectionSize(self._cmdlib_row_height)
        self.cmdlib_table.verticalHeader().setMinimumSectionSize(self._cmdlib_row_height)
        self.cmdlib_table.setRowCount(self.CMDLIB_MAX)
        self.cmdlib_table.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
        self.cmdlib_table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.cmdlib_table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.cmdlib_table.setEditTriggers(
            QAbstractItemView.DoubleClicked
            | QAbstractItemView.SelectedClicked
            | QAbstractItemView.EditKeyPressed
        )

        header = self.cmdlib_table.horizontalHeader()
        header_font = QFont(self._cmdlib_table_font)
        header_font.setWeight(QFont.Weight.DemiBold)
        header.setFont(header_font)
        header.setMinimumHeight(max(30, QFontMetrics(header_font).height() + 10))
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.Fixed)
        # 实际列宽在固定行按钮创建后，按按钮文字和字体自动确定。
        self.cmdlib_table.setColumnWidth(2, _CMDLIB_ACTION_COLUMN_WIDTH)

        # 不覆盖 TableWidget 自身的 Fluent delegate。只对可编辑列安装
        # 列级委托，从而保留 setHoverRow/pressedRow 等内部协议。
        base_delegate = self.cmdlib_table.itemDelegate()
        self._cmdlib_name_delegate = CommandLibraryCellDelegate(
            self.cmdlib_table, base_delegate, draw_right_separator=True
        )
        self._cmdlib_payload_delegate = CommandLibraryCellDelegate(
            self.cmdlib_table, base_delegate, draw_right_separator=False
        )
        self.cmdlib_table.setItemDelegateForColumn(0, self._cmdlib_name_delegate)
        self.cmdlib_table.setItemDelegateForColumn(1, self._cmdlib_payload_delegate)

        self._cmdlib_refreshing = False
        self._cmdlib_initialize_rows()
        self.cmdlib_table.itemChanged.connect(self._cmdlib_on_item_changed)
        layout.addWidget(self.cmdlib_table, stretch=1)

        return card

    def _build_send_card(self) -> CardWidget:
        """Build the shared send panel with a responsive grid.

        The old one-row QHBoxLayout forced three mode buttons, a text editor and
        the complete action matrix into one horizontal line.  On 125–200% DPI
        or a narrow logical desktop the right side was compressed and Chinese
        captions were cut in half.  This layout changes rows only; all send
        state and callbacks remain unchanged.
        """
        card = CardWidget()
        self.send_card_layout = QGridLayout(card)
        self.send_card_layout.setContentsMargins(10, 8, 10, 8)
        self.send_card_layout.setHorizontalSpacing(10)
        self.send_card_layout.setVerticalSpacing(8)

        # Mode selector.  Direction changes between vertical and horizontal in
        # _relayout_send_panel().
        mode_widget = QWidget(card)
        self.send_mode_widget = mode_widget
        self.send_mode_layout = QBoxLayout(QBoxLayout.Direction.TopToBottom, mode_widget)
        self.send_mode_layout.setContentsMargins(0, 0, 0, 0)
        self.send_mode_layout.setSpacing(6)

        self.btn_mode_proto = ToggleButton("协议模式")
        self.btn_mode_proto.setChecked(True)
        self.btn_mode_proto.clicked.connect(
            self._safe(lambda checked=False: self._set_send_mode("protocol"))
        )
        self.send_mode_layout.addWidget(self.btn_mode_proto)

        self.btn_mode_hex = ToggleButton("HEX")
        self.btn_mode_hex.clicked.connect(
            self._safe(lambda checked=False: self._set_send_mode("raw_hex"))
        )
        self.send_mode_layout.addWidget(self.btn_mode_hex)

        self.btn_mode_ascii = ToggleButton("ASCII")
        self.btn_mode_ascii.clicked.connect(
            self._safe(lambda checked=False: self._set_send_mode("raw_ascii"))
        )
        self.send_mode_layout.addWidget(self.btn_mode_ascii)

        for button in (self.btn_mode_proto, self.btn_mode_hex, self.btn_mode_ascii):
            fit_text_control(button)

        # Protocol/raw editors share one stacked position.  Use a minimum rather
        # than a fixed height so the panel may grow with system font metrics.
        center_widget = QWidget(card)
        self.send_center_widget = center_widget
        center = QVBoxLayout(center_widget)
        center.setContentsMargins(0, 0, 0, 0)
        center.setSpacing(0)

        editor_min_height = 72
        self.fields_edit = TextEdit()
        self.fields_edit.setObjectName("SendProtocolText")
        self.fields_edit.setStyleSheet(_TEXT_EDIT_FRAME_QSS)
        self.fields_edit.setPlaceholderText('协议字段 JSON，例如 {"value": 1}')
        self.fields_edit.setMinimumHeight(editor_min_height)
        self.fields_edit.setMaximumHeight(120)
        self.fields_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.fields_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        center.addWidget(self.fields_edit)

        self.raw_edit = TextEdit()
        self.raw_edit.setObjectName("SendRawText")
        self.raw_edit.setStyleSheet(_TEXT_EDIT_FRAME_QSS)
        self.raw_edit.setPlaceholderText("HEX 或 ASCII 原始数据")
        self.raw_edit.setMinimumHeight(editor_min_height)
        self.raw_edit.setMaximumHeight(120)
        self.raw_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.raw_edit.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.raw_edit.textChanged.connect(
            lambda: self._tx_input_validation_timer.start()
        )
        self.raw_edit.setVisible(False)
        center.addWidget(self.raw_edit)

        # Action controls.  Their grid is rebuilt at narrow widths so no fixed
        # three-column minimum can force the parent window wider than its screen.
        action_widget = QWidget(card)
        self.send_action_widget = action_widget
        self.send_action_layout = QGridLayout(action_widget)
        self.send_action_layout.setContentsMargins(0, 0, 0, 0)
        self.send_action_layout.setHorizontalSpacing(6)
        self.send_action_layout.setVerticalSpacing(6)

        self.btn_send_once = PrimaryPushButton("发送")
        self.btn_send_once.clicked.connect(self._safe(self._on_send_once))

        self.btn_clear_send = PushButton("清空输入")
        self.btn_clear_send.clicked.connect(self._safe(self._on_clear_send))

        self.btn_crlf = ToggleButton("加回车换行")
        self.btn_crlf.setChecked(bool(self.tx_append_crlf))
        self.btn_crlf.toggled.connect(self._safe(self._set_tx_append_crlf))

        self.btn_crc = ToggleButton("自动追加校验位")
        self.btn_crc.toggled.connect(lambda c: setattr(self, "tx_auto_crc8", c))

        self.crc_algo_combo = DpiAwareComboBox()
        crc_algorithms = [
            "ADD8", "0-ADD8", "XOR8", "ADD16",
            "ModbusCRC16", "CCITT-CRC16", "CRC32",
        ]
        self.crc_algo_combo.addItems(crc_algorithms)
        self.crc_algo_combo.currentTextChanged.connect(
            lambda t: setattr(self, "tx_crc_algo", t)
        )

        self.btn_cycle = ToggleButton("自动发送")
        self.btn_cycle.toggled.connect(self._safe(self._on_toggle_cycle_send))

        self.interval_label = BodyLabel("间隔ms")
        self.interval_spin = SpinBox()
        self.interval_spin.setRange(10, 3600000)
        self.interval_spin.setValue(1000)
        self.interval_spin.valueChanged.connect(
            lambda v: setattr(self, "tx_interval_ms", v)
        )

        action_font = _make_crisp_ui_font(
            _responsive_point_size(action_widget, base=_UI_FONT_POINT_SIZE, maximum=13)
        )
        for button in (
            self.btn_send_once,
            self.btn_clear_send,
            self.btn_crlf,
            self.btn_crc,
            self.btn_cycle,
        ):
            button.setFont(action_font)
            _fit_button_to_text(
                button,
                horizontal_padding=16,
                vertical_padding=5,
                minimum_height=30,
            )
        for widget in (self.interval_label, self.interval_spin, self.crc_algo_combo):
            widget.setFont(action_font)
        self.interval_spin.setMinimumHeight(30)
        self.crc_algo_combo.setMinimumHeight(30)

        combo_metrics = QFontMetrics(action_font)
        longest_algo = max(crc_algorithms, key=lambda value: combo_metrics.horizontalAdvance(value))
        self.crc_algo_combo.setMinimumWidth(
            max(118, combo_metrics.horizontalAdvance(longest_algo) + 54)
        )
        self.interval_spin.setMinimumWidth(
            max(118, combo_metrics.horizontalAdvance("3600000") + 66)
        )

        self._send_action_widgets = (
            self.send_mode_widget,
            self.btn_send_once,
            self.btn_clear_send,
            self.btn_crlf,
            self.btn_crc,
            self.crc_algo_combo,
            self.btn_cycle,
            self.interval_label,
            self.interval_spin,
        )
        self._send_panel_widgets = (mode_widget, center_widget, action_widget)
        self._relayout_send_panel(force=True)
        return card

    @staticmethod
    def _remove_widgets_from_layout(layout, widgets) -> None:
        if layout is None:
            return
        for widget in widgets:
            try:
                layout.removeWidget(widget)
            except Exception:
                pass

    def _relayout_send_actions(self, mode: str) -> None:
        """按档位装配发送动作区：wide 单行、medium 两行、narrow 两列。"""
        layout = getattr(self, "send_action_layout", None)
        widgets = getattr(self, "_send_action_widgets", ())
        if layout is None or not widgets:
            return
        self._remove_widgets_from_layout(layout, widgets)
        self._reset_grid_stretches(layout, 12)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)

        mode_widget = getattr(self, "send_mode_widget", None)
        if mode == "wide":
            positions = (
                (mode_widget, 0, 0, 1, 1),
                (self.btn_send_once, 0, 1, 1, 1),
                (self.btn_cycle, 0, 2, 1, 1),
                (self.interval_label, 0, 3, 1, 1),
                (self.interval_spin, 0, 4, 1, 1),
                # 第 5 列吸收剩余宽度。
                (self.btn_crlf, 0, 6, 1, 1),
                (self.btn_crc, 0, 7, 1, 1),
                (self.crc_algo_combo, 0, 8, 1, 1),
                (self.btn_clear_send, 0, 9, 1, 1),
            )
            layout.setColumnStretch(5, 1)
        elif mode == "medium":
            positions = (
                (mode_widget, 0, 0, 1, 1),
                (self.btn_send_once, 0, 1, 1, 1),
                (self.btn_cycle, 0, 2, 1, 1),
                (self.interval_label, 0, 3, 1, 1),
                (self.interval_spin, 0, 4, 1, 1),
                (self.btn_crlf, 1, 0, 1, 1),
                (self.btn_crc, 1, 1, 1, 1),
                (self.crc_algo_combo, 1, 2, 1, 1),
                (self.btn_clear_send, 1, 6, 1, 1),
            )
            # 第 5 列是纯弹性留白，避免任何按钮/下拉框被拉长。
            layout.setColumnStretch(5, 1)
        else:
            # 窄档中的模式选择器由 send_card_layout 单独放在编辑器上方。
            positions = (
                (self.btn_send_once, 0, 0, 1, 1),
                (self.btn_clear_send, 0, 1, 1, 1),
                (self.btn_crlf, 1, 0, 1, 2),
                (self.btn_crc, 2, 0, 1, 1),
                (self.crc_algo_combo, 2, 1, 1, 1),
                (self.btn_cycle, 3, 0, 1, 2),
                (self.interval_label, 4, 0, 1, 1),
                (self.interval_spin, 4, 1, 1, 1),
            )
            layout.setColumnStretch(0, 1)
            layout.setColumnStretch(1, 1)

        for widget, row, column, row_span, column_span in positions:
            if widget is None:
                continue
            layout.addWidget(widget, row, column, row_span, column_span)
            layout.setAlignment(widget, Qt.AlignmentFlag.AlignVCenter)
        layout.invalidate()

    def _relayout_send_panel(self, *, force: bool = False) -> None:
        """发送面板采用“上编辑器、下动作行”的紧凑两段式布局。"""
        layout = getattr(self, "send_card_layout", None)
        widgets = getattr(self, "_send_panel_widgets", ())
        if layout is None or not widgets:
            return
        card = getattr(self, "send_card", None)
        width = int(card.contentsRect().width()) if card is not None else int(self.width())
        if width <= 1:
            width = int(self.width())
        mode = "wide" if width >= 1040 else ("medium" if width >= 700 else "narrow")
        if not force and getattr(self, "_send_layout_mode", None) == mode:
            return
        self._send_layout_mode = mode
        self._remove_widgets_from_layout(layout, widgets)
        self._reset_grid_stretches(layout, 12)

        self.send_mode_layout.setDirection(QBoxLayout.Direction.LeftToRight)
        for index in range(self.send_mode_layout.count()):
            self.send_mode_layout.setStretch(index, 1)

        if mode == "narrow":
            layout.addWidget(self.send_mode_widget, 0, 0, 1, 4)
            layout.addWidget(self.send_center_widget, 1, 0, 1, 4)
            layout.addWidget(self.send_action_widget, 2, 0, 1, 4)
        else:
            # 宽/中档只有“编辑器行 + 动作行”两个直接子项。
            layout.addWidget(self.send_center_widget, 0, 0, 1, 4)
            layout.addWidget(self.send_action_widget, 1, 0, 1, 4)
        layout.setColumnStretch(0, 1)
        self._relayout_send_actions(mode)

        layout.invalidate()
        if card is not None:
            apply_adaptive_geometry(card, _UI_FONT_POINT_SIZE)
            card.updateGeometry()

    def _build_status_bar(self) -> QWidget:
        bar = QWidget()
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(4, 2, 4, 2)

        # 左右状态合并为一行，并始终在整个窗口底部水平居中。
        self.status_label = BodyLabel("")
        self.status_label.setTextFormat(Qt.RichText)
        self.status_label.setAlignment(Qt.AlignCenter)
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )
        layout.addWidget(self.status_label, stretch=1)
        self._refresh_status_bar()
        return bar

    # ================================================================
    # 安全包装 / 状态
    # ================================================================

    def _safe(self, fn: Callable):
        """统一保护 Qt 按钮/切换控件回调。

        Qt 的 ``clicked`` 信号通常会额外发送一个 ``checked: bool``。过去包装器
        无条件把该参数继续传给无参数函数，会造成点击“发送/监控/存储”等按钮时
        抛 ``TypeError``。这里根据目标函数签名只传它实际能接收的位置参数。
        可预期的输入错误用普通提示呈现；只有真正的程序异常才写入 error.log。
        """
        try:
            signature = inspect.signature(fn)
            positional = [
                p for p in signature.parameters.values()
                if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
            ]
            accepts_varargs = any(
                p.kind == p.VAR_POSITIONAL for p in signature.parameters.values()
            )
            max_positional = None if accepts_varargs else len(positional)
        except (TypeError, ValueError):
            max_positional = None

        def wrapper(*args, **kwargs):
            call_args = args if max_positional is None else args[:max_positional]
            try:
                return fn(*call_args, **kwargs)
            except ProtocolError as exc:
                friendly, debug = classify_protocol_error(exc)
                message = friendly
                if debug and debug != friendly:
                    message += f"\n\n原因：{debug}"
                QMessageBox.warning(self, "操作提示", message)
                return None
            except UserCorrectableError as exc:
                self._report_error("操作失败", exc)
                return None
            except Exception as exc:
                self._report_error("操作失败", exc)
                return None
        return wrapper

    def _report_error(self, title: str, exc: Exception) -> None:
        """Show user-correctable conditions as prompts; log only real faults."""
        presentation = build_user_error_presentation(title, exc)
        if presentation is not None:
            QMessageBox.warning(self, presentation.title, presentation.message)
            return

        friendly, debug = classify_protocol_error(exc)
        try:
            log_path = _log_error_to_disk(exc)
        except Exception:
            log_path = None
        body = friendly
        if debug and isinstance(exc, ProtocolError):
            body += f"\n\n原因: {debug}"
        if log_path is not None:
            body += f"\n\n详细日志: {log_path}"
        QMessageBox.critical(self, title, body)

    @staticmethod
    def _format_cache_size(size_bytes: int) -> str:
        size = max(0, int(size_bytes or 0))
        if size < 1024:
            return f"{size}B"
        if size < 1024 * 1024:
            return f"{size / 1024:.1f}KB"
        return f"{size / (1024 * 1024):.1f}MB"

    def _schedule_status_refresh(self) -> None:
        timer = getattr(self, "_status_refresh_timer", None)
        if timer is None:
            return
        if not timer.isActive():
            timer.start(120)

    def _refresh_status_bar(self) -> None:
        label = getattr(self, "status_label", None)
        if label is None:
            return

        connected = bool(self.is_collecting)
        saving = bool(self._save_raw_active)
        conn_color = "#22A447" if connected else "#F59E0B"
        save_color = "#22A447" if saving else "#F59E0B"
        conn_text = "已连接" if connected else "未连接"
        save_text = "存储中" if saving else "未存储"

        port = self.port_var.split(" - ")[0].strip() if self.port_var else "未选串口"
        baud = self.baudrate_var or "-"

        text_widget = getattr(self, "serial_text", None)
        if text_widget is not None:
            # blockCount() 为常数时间；缓存字节数在写入时增量维护，
            # 不再每 120ms 把整个文本框复制、编码和 splitlines。
            line_count = max(0, text_widget.document().blockCount() - 1)
            cache_size = max(0, int(self._display_utf8_bytes))
        else:
            cache_size = 0
            line_count = 0

        err = 0
        if self.collector and getattr(self.collector, "sync", None):
            err = getattr(self.collector.sync, "error_count", 0) or 0

        parts = [
            f'<span style="color:{conn_color};">●</span> {conn_text}',
            html.escape(port),
            html.escape(str(baud)),
            f'<span style="color:{save_color};">●</span> {save_text}',
            f"显示缓存约 {self._format_cache_size(cache_size)}",
            f"行数 {line_count}",
            f"RX {self.rx_frame_count}",
            f"TX {self.tx_frame_count}",
            f"错误 {err}",
        ]
        if self._last_status_message:
            message = self._last_status_message
            if len(message) > 48:
                message = message[:45] + "..."
            parts.append(html.escape(message))
        label.setText(" &nbsp;|&nbsp; ".join(parts))

    def _set_status(self, msg: str) -> None:
        self._last_status_message = str(msg or "").strip()
        self._schedule_status_refresh()

    def _update_stats_bar(self) -> None:
        self._schedule_status_refresh()

    # ================================================================
    # 协议加载（业务逻辑原样）
    # ================================================================

    def _load_protocols(self) -> None:
        """扫描全部协议，并分别维护 Word 产品与 JSON 产品列表。"""
        all_products: list[tuple[str, str, str]] = []
        get_builtin_v3(refresh=False)
        all_products.append(("串口3.0协议", "__builtin_v3__", "word"))

        directory = get_protocol_dir()
        if directory.exists():
            for file_path in sorted(directory.glob("*.json")):
                if file_path.name.lower() in ("v3_serial.json", "_template.json"):
                    continue
                try:
                    cfg = load_protocol(file_path)
                except Exception:
                    continue
                name = str(cfg.get("product") or file_path.stem)
                source_kind = str(cfg.get("import_source") or "word").strip().lower()
                if source_kind != "json":
                    source_kind = "word"
                all_products.append((name, str(file_path), source_kind))

        self._product_sources = {name: source for name, source, _ in all_products}
        self._product_kinds = {name: kind for name, _, kind in all_products}
        word_products = [item for item in all_products if item[2] == "word"]

        previous_word = ""
        combo = getattr(self, "product_combo", None)
        if combo is not None:
            previous_word = str(combo.currentText() or "").strip()
            combo.blockSignals(True)
            try:
                combo.clear()
                combo.addItems([name for name, _, _ in word_products])
                selected = previous_word if previous_word in self._product_sources and self._product_kinds.get(previous_word) == "word" else ""
                if not selected and word_products:
                    selected = word_products[0][0]
                if selected:
                    index = combo.findText(selected)
                    if index >= 0:
                        combo.setCurrentIndex(index)
            finally:
                combo.blockSignals(False)
        else:
            selected = word_products[0][0] if word_products else ""

        # 启动或停留在页面1时加载其 Word 协议；页面2导入 JSON 时不覆盖当前 JSON 配置。
        current_page = None
        try:
            current_page = self.stackedWidget.currentWidget()
        except Exception:
            pass
        if selected and (self.mcu_page is None or current_page is not self.mcu_page):
            self._load_product_cfg(selected)

        if self.mcu_page is not None:
            try:
                self.mcu_page.sync_products()
            except Exception:
                pass
        self._set_status(
            f"已加载 {len(word_products)} 个Word协议 / "
            f"{sum(1 for _, _, kind in all_products if kind == 'json')} 个JSON产品"
        )


    def _load_product_cfg(self, product_name: str) -> bool:
        """Load one protocol product transactionally.

        Parsing, attribute normalization and initial-value validation must all
        succeed before the active configuration is replaced.  A broken product
        therefore cannot leave the shared attribute center half-cleared or make
        the MCU auto-reply engine operate on a partially loaded definition.
        """
        source = getattr(self, "_product_sources", {}).get(product_name)
        if not source:
            return False

        try:
            if source == "__builtin_v3__":
                candidate_cfg = get_builtin_v3()
            else:
                from protocol_parser.parser import merge_protocol

                user_cfg = load_protocol(source)
                candidate_cfg = merge_protocol(get_builtin_v3(), user_cfg)
                if isinstance(user_cfg.get("product_info"), dict):
                    candidate_cfg["product_info"] = dict(user_cfg["product_info"])
                # 0x21 设备信息回复需要原始 services/properties 中的
                # SIID/PIID 来构建 6 字节属性映射表，合并协议后必须保留。
                if "source_function_json" in user_cfg:
                    candidate_cfg["source_function_json"] = user_cfg.get(
                        "source_function_json"
                    )
                if "device_info_expand_rules" in user_cfg:
                    candidate_cfg["device_info_expand_rules"] = user_cfg.get(
                        "device_info_expand_rules"
                    )
                candidate_cfg["import_source"] = str(
                    user_cfg.get("import_source") or "word"
                )
                if candidate_cfg["import_source"].strip().lower() == "json":
                    from protocol_parser.product_importer import localize_attributes

                    localize_attributes(candidate_cfg.get("attributes"))

            # Attribute loading now validates enum/range/string defaults.  Do it
            # before publishing self.cfg so a failure leaves the current product
            # and auto-reply context unchanged.
            old_cfg = self.cfg
            try:
                self._attr_center.load_product(candidate_cfg or {})
            except Exception:
                try:
                    self._attr_center.load_product(old_cfg or {})
                except Exception:
                    self._attr_center.load_product({})
                raise
        except Exception as exc:
            self._report_error("协议加载失败", exc)
            return False

        self.cfg = candidate_cfg
        self.product_var = product_name
        self._auto_reply.reset_state()
        self._sync_collector_cfg()
        if str((self.cfg or {}).get("import_source") or "").strip().lower() == "json":
            self._mcu_cfg = self.cfg or {}
            self._sync_mcu_collector_cfg()

        # 仅当页签2当前选中的就是该 JSON 产品时刷新页签2，避免页面1切换 Word
        # 产品时把页签2的产品选择和属性内容强制同步过去。
        if self.mcu_page is not None:
            try:
                if self.mcu_page.product_combo.currentText() == product_name:
                    self.mcu_page.refresh_attr_table()
                    self.mcu_page.refresh_current_values()
            except Exception:
                pass
        self._set_status(f"已加载: {product_name}")
        return True


    def _on_product_change(self, name: str) -> None:
        if not name:
            return
        if getattr(self, "_product_kinds", {}).get(name, "word") == "json":
            return
        self._load_product_cfg(name)


    def _import_docx(self) -> None:
        from protocol_parser.docx_importer import import_from_docx
        from protocol_parser.attr_editor import AttributeEditorDialog
        path, _ = QFileDialog.getOpenFileName(self, "选择 Word 协议文档", "", "Word 文档 (*.docx);;所有文件 (*.*)")
        if not path:
            return
        try:
            imported_cfg = import_from_docx(path)
        except Exception as e:
            self._report_error("导入失败", e)
            return
        warnings = imported_cfg.get("_import_warnings") or []
        if warnings:
            msg = "⚠ 导入时发现以下问题：\n\n" + "\n".join(f"{i}. {w}" for i, w in enumerate(warnings, 1))
            QMessageBox.warning(self, "Word 导入告警（不影响继续编辑/保存）", msg)

        dlg = AttributeEditorDialog(self, imported_cfg)
        if dlg.exec() != QDialog.Accepted or not dlg.result:
            return

        user_cfg = dlg.result
        user_cfg["import_source"] = "word"
        protocol_name = user_cfg.get("product", Path(path).stem)
        save_path = get_protocol_dir() / f"{protocol_name}.json"
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(user_cfg, f, ensure_ascii=False, indent=2)
        self._load_protocols()
        # 选中新导入的协议
        idx = self.product_combo.findText(protocol_name)
        if idx >= 0:
            self.product_combo.setCurrentIndex(idx)
        self._set_status(f"已导入: {protocol_name}")

    def _edit_or_delete_word_protocol(self) -> None:
        """编辑或删除页面1当前选中的 Word 协议。"""
        from protocol_parser.attr_editor import AttributeEditorDialog

        current_name = str(self.product_combo.currentText() or "").strip()
        if not current_name:
            QMessageBox.information(self, "提示", "当前未选择协议")
            return
        source = str(getattr(self, "_product_sources", {}).get(current_name) or "")
        if not source or source == "__builtin_v3__":
            QMessageBox.information(self, "提示", "内置串口3.0协议不能修改或删除")
            return
        source_path = Path(source)
        if not source_path.exists():
            QMessageBox.warning(self, "提示", "找不到当前协议文件")
            return

        try:
            raw_cfg = json.loads(source_path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            self._report_error("读取当前协议失败", exc)
            return

        dlg = AttributeEditorDialog(self, raw_cfg, allow_delete=True)
        dlg.exec()

        if dlg.delete_requested:
            self._delete_word_protocol(current_name, source_path)
            return
        if dlg.result is None:
            return

        user_cfg = dlg.result
        user_cfg["import_source"] = "word"
        try:
            with open(source_path, "w", encoding="utf-8") as f:
                json.dump(user_cfg, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            self._report_error("保存协议失败", exc)
            return
        self._load_protocols()
        self._set_status(f"已修改: {current_name}")

    def _delete_word_protocol(self, protocol_name: str, source_path: Path) -> None:
        """确认后把 Word 协议文件移入回收站并刷新列表。"""
        answer = _QtMessageBox.question(
            self,
            "删除协议",
            f"确定删除协议“{protocol_name}”吗？\n\n{source_path.name}\n删除后将移入回收站，可恢复。",
            _QtMessageBox.StandardButton.Yes | _QtMessageBox.StandardButton.No,
            _QtMessageBox.StandardButton.No,
        )
        if answer != _QtMessageBox.StandardButton.Yes:
            return

        if _move_to_recycle_bin(source_path):
            removed = True
        else:
            confirm = _QtMessageBox.question(
                self,
                "无法移入回收站",
                f"无法把文件移入回收站，是否永久删除“{protocol_name}”？",
                _QtMessageBox.StandardButton.Yes | _QtMessageBox.StandardButton.No,
                _QtMessageBox.StandardButton.No,
            )
            if confirm != _QtMessageBox.StandardButton.Yes:
                return
            try:
                source_path.unlink()
                removed = True
            except Exception as exc:
                self._report_error("删除协议失败", exc)
                return

        if removed:
            self._load_protocols()
            self._set_status(f"已删除: {protocol_name}")


    def _generate_product_commands(self, cfg: dict) -> list[str]:
        """兼容旧接口；产品/Word 导入不再生成或写入指令库。

        指令库自 v3.1.0 BuildFix29 起完全由用户手工维护。模拟 MCU 页的
        “预置命令”仍由其自身业务模块生成，与此指令库互不关联。
        """
        del cfg
        return []

    def _fill_auto_cmdlib(self, commands: list[tuple[str, str]]) -> None:
        """兼容旧接口，但不再把导入内容写入指令库。

        v3.1.0 BuildFix29 起，指令库只保存用户手工录入的内容。
        """
        del commands

    def _show_protocol(self) -> None:
        if not self.cfg:
            return
        content = json.dumps(self.cfg, ensure_ascii=False, indent=2)
        dlg = QDialog(self)
        apply_fluent_dialog_style(dlg)
        dlg.setWindowTitle(f"协议详情 - {self.cfg.get('product', '')}")
        dlg.resize(800, 600)
        lay = QVBoxLayout(dlg)
        te = TextEdit()
        te.setStyleSheet(_TEXT_EDIT_FRAME_QSS)
        te.setPlainText(content)
        te.setReadOnly(True)
        lay.addWidget(te)
        apply_adaptive_geometry(dlg)
        fit_window_to_screen(
            dlg, preferred=(800, 600), minimum=(520, 360), margin=(36, 72)
        )
        dlg.exec()

    # ================================================================
    # 串口控制（业务逻辑原样，仅替换 UI 调用）
    # ================================================================

    def _refresh_ports(self, *, silent: bool = False) -> bool:
        ports = SerialCollector.list_ports()
        display_list = []
        for p in ports:
            dev = p.get("device", "")
            desc = p.get("description", "")
            if desc and desc != dev:
                display_list.append(f"{dev} - {desc}")
            else:
                display_list.append(dev)

        def _com_sort_key(item: str):
            m = re.match(r"^COM(\d+)", str(item), re.I)
            if m:
                return (0, int(m.group(1)))
            return (1, str(item).lower())

        display_list.sort(key=_com_sort_key)
        devices = [d.split(" - ")[0].strip() for d in display_list]
        changed = devices != getattr(self, "_last_port_devices", None)
        self._last_port_devices = devices

        cur = self.port_combo.currentText()
        current_device = cur.split(" - ")[0].strip() if cur else ""
        current_disappeared = bool(
            current_device and current_device not in devices and self.is_collecting
        )
        self.port_combo.blockSignals(True)
        self.port_combo.clear()
        self.port_combo.addItems(display_list)
        if current_disappeared:
            # Do not silently switch a live session to the first unrelated COM
            # port. Keep the disconnected device visible until the collector
            # reports the connection error or the user stops monitoring.
            self.port_combo.insertItem(0, cur)
            self.port_combo.setCurrentIndex(0)
        elif cur:
            idx = self.port_combo.findText(cur)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)
            elif display_list:
                self.port_combo.setCurrentIndex(0)
        elif display_list:
            self.port_combo.setCurrentIndex(0)
        self.port_combo.blockSignals(False)
        self.port_var = self.port_combo.currentText()

        if current_disappeared:
            message = f"监控中的串口 {current_device} 已从系统端口列表消失，可能已拔出"
            self._enqueue_display_text(f"[警告] {message}\n", color=PALETTE["error"])
            self._set_status(message)

        if changed and not silent:
            self._set_status(f"找到 {len(ports)} 个串口")
        return changed

    def _poll_ports(self) -> None:
        try:
            self._refresh_ports(silent=True)
        except Exception:
            pass

    def _cancel_serial_reconnect(self, *, reset_attempts: bool = True) -> None:
        timer = getattr(self, "_serial_reconnect_timer", None)
        if timer is not None and timer.isActive():
            timer.stop()
        if reset_attempts:
            self._serial_reconnect_attempt = 0
        if not self.is_collecting:
            try:
                self.btn_start.setText("● 开始监控")
            except Exception:
                pass

    def _on_port_changed(self, text: str) -> None:
        self.port_var = text
        self._cancel_serial_reconnect()
        if self.is_collecting:
            self._serial_manual_stop = True
            self._stop_serial(after_stop=self._restart_serial_after_setting_change)
        else:
            self._serial_reconnect_params = None
            self._refresh_status_bar()

    def _on_baud_text_changed(self, text: str) -> None:
        """记录编辑中的波特率文本，不触碰正在运行的串口。"""
        self.baudrate_var = str(text or "").strip()
        if not self.is_collecting:
            self._refresh_status_bar()

    def _commit_baud(self) -> None:
        """在用户明确提交后校验并应用波特率。"""
        text = str(self.baudrate_var or self.baud_combo.currentText()).strip()
        try:
            value = int(text)
        except (TypeError, ValueError):
            value = 0
        if value <= 0:
            QMessageBox.warning(self, "提示", "波特率必须是正整数")
            previous = str(self.baudrate_var_last_valid or "9600")
            self.baud_combo.blockSignals(True)
            self.baud_combo.setCurrentText(previous)
            self.baud_combo.blockSignals(False)
            self.baudrate_var = previous
            return
        normalized = str(value)
        if normalized == str(self.baudrate_var_last_valid):
            self.baudrate_var = normalized
            return
        self.baudrate_var_last_valid = normalized
        self.baudrate_var = normalized
        self._on_baud_changed(normalized)

    def _on_baud_changed(self, text: str) -> None:
        """应用已校验的波特率；只由 _commit_baud 调用。"""
        self.baudrate_var = str(text or "").strip()
        self._cancel_serial_reconnect()
        if self.is_collecting:
            self._serial_manual_stop = True
            self._stop_serial(after_stop=self._restart_serial_after_setting_change)
        else:
            self._serial_reconnect_params = None
            self._refresh_status_bar()

    def _toggle_serial(self) -> None:
        if self._serial_stopping:
            self._set_status("串口正在停止，请稍候…")
            return
        if not self.is_collecting and self._stopping_collector is not None:
            self._retry_stopping_collector()
            return
        reconnect_timer = getattr(self, "_serial_reconnect_timer", None)
        if not self.is_collecting and reconnect_timer is not None and reconnect_timer.isActive():
            self._serial_manual_stop = True
            self._cancel_serial_reconnect()
            self._set_status("已取消自动重连")
            return
        if self.is_collecting:
            self._serial_manual_stop = True
            self._stop_serial()
        else:
            self._serial_manual_stop = False
            self._start_serial()

    def _retry_stopping_collector(self) -> None:
        """Retry a timed-out stop without allowing a duplicate port open."""
        collector = self._stopping_collector
        if collector is None or self._serial_stopping:
            return
        generation = self._collector_generation
        self._serial_stopping = True
        self.btn_start.setText("正在重试停止…")
        self.btn_start.setEnabled(False)
        self._set_status("正在重试停止串口线程…")
        collector.stop_async(
            timeout=3.0,
            on_complete=lambda: self.bridge.collector_stopped_signal.emit(
                generation, None, None
            ),
            on_error=lambda exc: self.bridge.collector_stopped_signal.emit(
                generation, None, exc
            ),
        )

    def _restart_serial_after_setting_change(self) -> None:
        self._serial_manual_stop = False
        self._start_serial()

    def _start_serial(self, *, is_reconnect: bool = False) -> bool:
        if self._serial_stopping:
            self._set_status("串口正在停止，请稍候…")
            return False
        cfg = self.cfg if self.cfg else {}
        no_protocol = not self.cfg

        if is_reconnect and self._serial_reconnect_params:
            params = dict(self._serial_reconnect_params)
            port_display = str(params.get("port_display") or params.get("port") or "")
            port = str(params.get("port") or port_display.split(" - ")[0].strip())
            baudrate = int(params.get("baudrate") or 9600)
            bytesize = int(params.get("bytesize") or 8)
            stopbits = float(params.get("stopbits") or 1)
        else:
            port_display = self.port_var
            if not port_display:
                QMessageBox.warning(self, "提示", "请选择串口")
                return False
            port = port_display.split(" - ")[0].strip()
            try:
                baudrate = int(str(self.baudrate_var).strip())
                if baudrate <= 0:
                    raise ValueError
            except Exception:
                QMessageBox.warning(self, "提示", "波特率必须填写正整数")
                return False
            try:
                bytesize = int(self.bytesize_combo.currentText())
            except Exception:
                bytesize = 8
            try:
                stopbits = float(self.stopbits_combo.currentText())
            except Exception:
                stopbits = 1
            self._serial_reconnect_params = {
                "port_display": port_display,
                "port": port,
                "baudrate": baudrate,
                "bytesize": bytesize,
                "stopbits": stopbits,
            }

        self._cancel_serial_reconnect(reset_attempts=not is_reconnect)
        self._serial_manual_stop = False
        self._collector_generation += 1
        generation = self._collector_generation
        # Resolve and cache the monitored page before the worker thread starts.
        # The serial callback must not read Qt widgets from its background thread.
        try:
            self._monitoring_page = (
                1 if self.stackedWidget.currentWidget() is self.mcu_page else 0
            )
        except Exception:
            self._monitoring_page = 0
        mcu_enabled = bool(
            self._monitoring_page == 1
            and self._is_mcu_auto_reply_context_active()
        )
        self._set_status(f"正在{'重新' if is_reconnect else ''}连接 {port} @ {baudrate}...")

        def on_frame(result, frame, ts):
            if generation != self._collector_generation:
                return
            try:
                self.bridge.frame_signal.emit(result, ts)
                self._write_raw_data(frame.raw, ts)
            except Exception as e:
                _log_error_to_disk(e)

        def on_mcu_frame(result, frame, ts):
            """MCU-only RX channel; never active on the receive-analysis page.

            A 0x01 write command is transactional: the automatic-reply engine
            validates the complete command, updates the attribute center, then
            queues ACK/report frames.  It must run before any generic display
            update, otherwise an invalid multi-attribute command could partly
            change state and still be rejected afterwards.
            """
            if generation != self._collector_generation or not mcu_enabled:
                return
            try:
                changed: list[int] = []
                cmd_int = -1

                # If a legacy/custom product command definition fails to parse
                # the 0x01 data section, recover a minimal result from the
                # already synchronized and checksum-checked frame.  The reply
                # engine will decode the raw attr list with the shared parser.
                if (
                    result is None
                    and int(getattr(frame, "cmd_code", -1)) == 0x01
                    and getattr(frame, "checksum_ok", None) is not False
                    and bytes(getattr(frame, "data", b"") or b"")
                ):
                    raw_data = bytes(frame.data)
                    result = SimpleNamespace(
                        cmd_code="0x01",
                        direction="模组→MCU",
                        fields=[{
                            "name": "消息id",
                            "type": "uint8",
                            "value": raw_data[0],
                            "text": str(raw_data[0]),
                        }],
                    )

                if result is not None:
                    raw_cmd = getattr(result, "cmd_code", -1)
                    try:
                        cmd_int = (
                            int(str(raw_cmd), 16)
                            if str(raw_cmd).lower().startswith("0x")
                            else int(raw_cmd)
                        ) & 0xFF
                    except (TypeError, ValueError):
                        cmd_int = -1

                    if cmd_int == 0x01:
                        sent_count = 0
                        if self._auto_reply.enabled:
                            sent_count = self._auto_reply.on_frame(result, frame, ts)
                        else:
                            # 自动回复关闭时仍应把合法的模组写命令同步到属性中心；
                            # 旧逻辑完全跳过，导致实时属性永远不更新。
                            changed = self._attr_center.update_from_frame(result)
                        if sent_count:
                            changed = self._auto_reply.last_applied_attrids
                    else:
                        changed = self._attr_center.update_from_frame(result)
                        if self._auto_reply.enabled:
                            self._auto_reply.on_frame(result, frame, ts)

                    if changed:
                        self.bridge.attr_updated_signal.emit(changed)

                self.bridge.mcu_data_signal.emit(result, frame.raw, ts, False, False)
                self._write_raw_data(frame.raw, ts)
            except Exception as e:
                _log_error_to_disk(e)

        def on_error(msg):
            if generation != self._collector_generation:
                return
            try:
                self.bridge.error_signal.emit(msg)
            except Exception as e:
                try:
                    _log_error_to_disk(e)
                except Exception:
                    pass

        def on_connection_error(msg, kind="io"):
            # 连接级错误单独携带 generation。这样旧句柄的迟到错误不会
            # 停止或重连已经建立的新连接。
            try:
                self.bridge.collector_error_signal.emit(
                    generation, str(msg), str(kind or "io")
                )
            except Exception as e:
                try:
                    _log_error_to_disk(e)
                except Exception:
                    pass

        def on_raw(data, ts):
            if generation != self._collector_generation:
                return
            try:
                self.bridge.raw_signal.emit(data, ts)
                self._write_raw_data(data, ts)
            except Exception as e:
                try:
                    _log_error_to_disk(e)
                except Exception:
                    pass

        def on_tx_sent(
            data_sent: bytes,
            direction_label: str,
            ts: float,
            metadata: dict | None = None,
        ):
            del direction_label
            if generation != self._collector_generation:
                return
            try:
                self._write_raw_data(data_sent, ts, prefix="TX ")
                meta = metadata or {}
                is_auto_reply_tx = bool(meta.get("auto_reply"))
                if mcu_enabled:
                    result = None
                    if self._mcu_cfg:
                        try:
                            result = parse_frame(data_sent, self._mcu_cfg, direction="response")
                        except Exception:
                            result = None
                    self.bridge.mcu_data_signal.emit(
                        result, data_sent, ts, True, is_auto_reply_tx
                    )
                self.bridge.tx_signal.emit(data_sent, ts, meta)
            except Exception as e:
                _log_error_to_disk(e)

        direction = None
        if self.hex_format:
            if self.serial_sender == "模组发送":
                direction = "request"
            elif self.serial_sender == "MCU发送":
                direction = "response"

        is_ascii = not self.hex_format
        try:
            self.collector = SerialCollector(
                cfg=cfg,
                port=port,
                baudrate=baudrate,
                bytesize=bytesize,
                stopbits=stopbits,
                direction=direction,
                on_frame=on_frame,
                on_error=on_error,
                on_connection_error=on_connection_error,
                on_raw=on_raw,
                mcu_cfg=self._mcu_cfg if mcu_enabled else {},
                mcu_direction="request",
                on_mcu_frame=on_mcu_frame if mcu_enabled else None,
                primary_enabled=not mcu_enabled,
                raw_mode=(is_ascii or self.view_mode == "raw"),
                on_tx_sent=on_tx_sent,
            )
            self._auto_reply.set_collector(self.collector if mcu_enabled else None)
            self._attr_center.reset_heartbeat_counter()
            self.collector.start()
        except Exception as e:
            collector = self.collector
            self.collector = None
            self._auto_reply.set_collector(None)
            self._monitoring_page = None
            try:
                if collector is not None:
                    collector.stop()
            except Exception:
                pass
            error_kind = str(getattr(e, "kind", "io") or "io")
            if not is_reconnect:
                self._report_error("串口打开失败", e)
                self._set_status("就绪")
            else:
                try:
                    _log_error_to_disk(e)
                except Exception:
                    pass
                if error_kind == "busy":
                    self._serial_manual_stop = True
                    self._cancel_serial_reconnect()
                    self._enqueue_display_text(
                        f"[错误] {e}。串口被占用，已取消自动重连。\n",
                        color=PALETTE["error"],
                    )
                    self.btn_start.setText("● 开始监控")
                    self._set_status("串口被占用，请关闭其他程序后手动重试")
            return False

        self.is_collecting = True
        self._serial_reconnect_attempt = 0
        self.btn_start.setText("✓ 停止监控")
        mode_label = "ASCII" if is_ascii else "HEX"
        proto_tag = " (无协议·通用模式)" if no_protocol else ""
        reconnect_tag = "（自动重连成功）" if is_reconnect else ""
        if self.save_raw_enabled and not self._save_raw_active:
            self._open_save_raw_file()
            self._set_status(
                f"监控中: {port} @ {baudrate} ({mode_label}){proto_tag} - 保存原始数据{reconnect_tag}"
            )
        else:
            self._set_status(
                f"监控中: {port} @ {baudrate} ({mode_label}){proto_tag}{reconnect_tag}"
            )
        return True

    def _stop_serial(self, after_stop: Callable[[], None] | None = None) -> None:
        if self._serial_stopping:
            return
        self._cancel_serial_reconnect(reset_attempts=False)
        self._collector_generation += 1
        generation = self._collector_generation
        collector = self.collector
        self.collector = None
        self._stopping_collector = collector
        self._auto_reply.set_collector(None)
        self.is_collecting = False
        self._monitoring_page = None

        try:
            self._cmdlib_stop_cycle()
        except Exception as exc:
            _log_error_to_disk(exc)
        if self._tx_cycle_timer:
            self._tx_cycle_timer.stop()
            self._tx_cycle_timer = None
        self.tx_cycle = False

        if collector is None:
            self.bridge.collector_stopped_signal.emit(generation, after_stop, None)
            return

        self._serial_stopping = True
        self.btn_start.setText("正在停止…")
        self.btn_start.setEnabled(False)
        self._set_status("正在异步停止串口，请稍候…")
        collector.stop_async(
            timeout=3.0,
            on_complete=lambda: self.bridge.collector_stopped_signal.emit(
                generation, after_stop, None
            ),
            on_error=lambda exc: self.bridge.collector_stopped_signal.emit(
                generation, after_stop, exc
            ),
        )

    @Slot(int, object, object)
    def _on_collector_stopped(self, generation: int, after_stop, error) -> None:
        if generation != self._collector_generation:
            return
        if error is not None:
            self._serial_stopping = False
            self.btn_start.setText("重试停止")
            self.btn_start.setEnabled(True)
            self._set_status(f"串口停止失败：{error}")
            _log_error_to_disk(error)
            QMessageBox.warning(
                self,
                "串口停止提示",
                f"串口线程未能安全停止。为避免重复打开同一串口，当前窗口不会启动新会话。\n"
                f"请点击“重试停止”；仍失败时可关闭程序并重新插拔设备。\n\n{error}",
            )
            return

        self._stopping_collector = None
        self._serial_stopping = False
        try:
            self._close_save_raw_file()
        except Exception as exc:
            _log_error_to_disk(exc)
        self.save_raw_count = 0
        self.btn_start.setEnabled(True)
        self.btn_start.setText("● 开始监控")
        self._set_status("已停止")
        if callable(after_stop):
            try:
                after_stop()
            except Exception as exc:
                self._report_error("串口重启失败", exc)


    def _schedule_serial_reconnect(self, reason: str) -> None:
        """连接级故障后短延迟自动重连，最多尝试固定次数。"""
        if self._serial_manual_stop or not self._serial_reconnect_params:
            return
        if self._serial_reconnect_attempt >= self._serial_reconnect_max_attempts:
            self._enqueue_display_text(
                "[错误] 自动重连已达到最大次数，请检查 USB 线、供电或驱动后手动开始监控。\n",
                color=PALETTE["error"],
            )
            self.btn_start.setText("● 开始监控")
            self._set_status("自动重连失败，请手动重试")
            return

        self._serial_reconnect_reason = reason
        self._serial_reconnect_attempt += 1
        delay_ms = min(5000, 1000 * self._serial_reconnect_attempt)
        self.btn_start.setText("取消重连")
        self._set_status(
            f"串口连接异常，{delay_ms / 1000:g} 秒后自动重连 "
            f"({self._serial_reconnect_attempt}/{self._serial_reconnect_max_attempts})"
        )
        self._serial_reconnect_timer.start(delay_ms)

    def _attempt_serial_reconnect(self) -> None:
        if self._serial_manual_stop or self.is_collecting or self._serial_stopping:
            return
        success = self._start_serial(is_reconnect=True)
        if not success and not self._serial_manual_stop:
            self._schedule_serial_reconnect(self._serial_reconnect_reason)

    # ================================================================
    # UI 回调（主线程）
    # ================================================================

    @Slot(object, float)
    def _on_ui_frame(self, result: ParseResult, ts: float) -> None:
        self.rx_frame_count += 1
        self._display_serial_frame(result, ts)
        self._update_stats_bar()

    @Slot(bytes, float)
    def _on_ui_raw(self, data: bytes, ts: float) -> None:
        self._display_raw_data(data, ts)

    @Slot(int, str, str)
    def _on_collector_error(self, generation: int, msg: str, kind: str) -> None:
        """处理当前串口句柄的连接级故障，并自动尝试恢复。"""
        if generation != self._collector_generation or self._serial_manual_stop:
            return
        kind = str(kind or "io")
        if kind == "busy":
            self._enqueue_display_text(
                f"[错误] {msg}。串口被占用时自动重连不会恢复，请关闭其他串口程序后手动重试。\n",
                color=PALETTE["error"],
            )
            self._serial_manual_stop = True
            self._cancel_serial_reconnect()
            self._stop_serial()
            QMessageBox.warning(self, "串口被占用", msg)
            return
        suffix = "，等待设备重新连接…" if kind in ("not_found", "unplugged") else "，正在尝试自动重连…"
        self._enqueue_display_text(f"[警告] {msg}{suffix}\n", color=PALETTE["error"])
        self._serial_manual_stop = False
        self._stop_serial(after_stop=lambda: self._schedule_serial_reconnect(msg))

    @Slot(str)
    def _on_ui_error(self, msg: str) -> None:
        # 普通协议解析或业务回调异常只提示，不再无条件断开串口。
        self._enqueue_display_text(f"[错误] {msg}\n", color=PALETTE["error"])

    @Slot(bytes, float, object)
    def _on_ui_tx(self, data_sent: bytes, ts: float, metadata=None) -> None:
        self.tx_frame_count += 1
        self._display_serial_tx(data_sent, ts, metadata)
        self._update_stats_bar()

    @Slot(object, object, float, bool, bool)
    def _on_mcu_data(
        self,
        result,
        raw,
        ts: float,
        is_tx: bool,
        auto_reply: bool,
    ) -> None:
        """把串口数据转发给页签2（GUI线程）。"""
        if self.mcu_page is None:
            return
        try:
            self.mcu_page.on_data(
                result, raw, ts, is_tx=is_tx, auto_reply=auto_reply
            )
        except Exception as exc:
            _log_error_to_disk(exc)

    @Slot(object)
    def _on_attr_updated(self, changed_ids) -> None:
        """Refresh only cells whose attribute IDs actually changed."""
        if self.mcu_page is not None:
            try:
                self.mcu_page.refresh_current_values(changed_ids or [])
            except Exception as exc:
                _log_error_to_disk(exc)

    def _append_text_segments(self, segments: list[tuple[str, str | None]]) -> None:
        """一次取得光标并批量插入多个颜色片段，降低高频 UI 更新成本。"""
        if not segments:
            return
        scroll_bar = self.serial_text.verticalScrollBar()
        saved_scroll_value = scroll_bar.value()
        # Append with a document cursor instead of moving the QTextEdit's
        # visible cursor.  Otherwise Qt follows the cursor to the bottom even
        # after the auto-scroll toggle has been turned off.
        cursor = QTextCursor(self.serial_text.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        current_color: str | None = None
        total_bytes = 0
        for text, color in segments:
            if not text:
                continue
            if color != current_color:
                fmt = QTextCharFormat()
                if color:
                    fmt.setForeground(QColor(color))
                cursor.setCharFormat(fmt)
                current_color = color
            cursor.insertText(text)
            total_bytes += len(text.encode("utf-8", errors="replace"))

        self._display_utf8_bytes += total_bytes
        # 文档达到上限后 Qt 会自动删除最早的块；给缓存值设置合理上限，
        # 避免状态栏数字无限增长，同时不做全量文本扫描。
        self._display_utf8_bytes = min(self._display_utf8_bytes, 32 * 1024 * 1024)
        if self.autoscroll:
            self.serial_text.setTextCursor(cursor)
            self.serial_text.ensureCursorVisible()
        else:
            scroll_bar.setValue(saved_scroll_value)
        self._schedule_status_refresh()

    def _append_text(self, text: str, color: str | None = None) -> None:
        self._append_text_segments([(text, color)])

    def _enqueue_display_text(self, text: str, color: str | None = None) -> None:
        if not text:
            return
        self._disp_buf.append((text, color))
        self._disp_buf_chars += len(text)
        # 防止主线程暂时繁忙时缓冲无限增大；保留最近的数据。
        if self._disp_buf_chars > 512_000:
            kept: list[tuple[str, str | None]] = []
            chars = 0
            for item in reversed(self._disp_buf):
                kept.append(item)
                chars += len(item[0])
                if chars >= 256_000:
                    break
            kept.reverse()
            self._disp_buf = kept
            self._disp_buf_chars = chars
        if not self._disp_flush_timer.isActive():
            self._disp_flush_timer.start()

    def _flush_display_buf(self) -> None:
        if not self._disp_buf:
            return
        segments = self._disp_buf
        self._disp_buf = []
        self._disp_buf_chars = 0
        self._append_text_segments(segments)

    def _display_serial_frame(self, result: ParseResult, ts: float) -> None:
        ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        ok = result.error is None and result.checksum_ok is not False
        cs = "✓" if result.checksum_ok else ("✗" if result.checksum_ok is False else " ")
        status = "OK" if result.error is None else "ERR"
        raw_display = result.raw_hex
        if not self.hex_format:
            try:
                raw_bytes = bytes.fromhex(raw_display.replace(" ", ""))
                raw_display = "".join(chr(b) if 32 <= b < 127 else "." for b in raw_bytes)
            except Exception:
                pass

        line = f"[{ts_str}] {status} {cs} {result.cmd_code:<6} {result.cmd_name}"
        if result.direction:
            line += f" [{result.direction}]"
        data_fields = []
        in_data = False
        for f in result.fields:
            ftype = f.get("type", "")
            fname = f.get("name", "")
            ftext = f.get("text", "")
            if ftype == "separator":
                in_data = True
                continue
            if in_data and ftype not in ("header", "version", "cmd", "length", "checksum"):
                if isinstance(fname, str) and fname.startswith("attrid_"):
                    continue
                if ftext:
                    data_fields.append(f"{fname}={ftext}")
        if data_fields:
            line += f"  {{ {', '.join(data_fields)} }}"
        line += f"  | {raw_display}\n"
        color = PALETTE["success"] if ok else PALETTE["error"]
        self._enqueue_display_text(line, color=color)

    def _display_serial_tx(self, data_sent: bytes, ts: float, metadata=None) -> None:
        """按本次发送来源自己的格式显示 TX，不借用其他面板状态。

        发送面板和指令库分别把 ``display_format`` 写入发送元数据，
        因而两者的 HEX/ASCII 选择完全独立。旧调用未携带元数据时，
        才回退到接收页的 HEX 显示按钮状态。
        """
        ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        meta = metadata if isinstance(metadata, dict) else {}
        display_format = str(meta.get("display_format") or "").strip().upper()
        if display_format not in {"HEX", "ASCII"}:
            display_format = "HEX" if self.hex_format else "ASCII"

        if display_format == "ASCII":
            text = bytes(data_sent).decode("utf-8", errors="replace")
            # 将控制字符显式显示，避免 CR/LF 把一条 TX 记录拆成多行。
            shown = (
                text.replace("\\", "\\\\")
                .replace("\r", "\\r")
                .replace("\n", "\\n")
                .replace("\t", "\\t")
            )
            line = f"[{ts_str}] [TX] Raw-ASCII  | {shown}\n"
        else:
            shown = " ".join(f"{b:02X}" for b in data_sent)
            line = f"[{ts_str}] [TX] Raw-HEX    | {shown}\n"
        self._enqueue_display_text(line, color=PALETTE["tx"])

    def _display_raw_data(self, data: bytes, ts: float) -> None:
        ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
        parts = []
        if self.hex_format:
            for i in range(0, len(data), 16):
                chunk = data[i:i + 16].hex(" ").upper()
                parts.append(f"[{ts_str}] {chunk}\n")
        else:
            text = data.decode("utf-8", errors="replace")
            for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
                if not line:
                    continue
                printable = "".join(ch if (32 <= ord(ch) < 127 or ch == "\t") else "." for ch in line)
                parts.append(f"[{ts_str}] {printable}\n")
        if parts:
            self._enqueue_display_text("".join(parts), color=PALETTE["field"])

    def _clear_output(self) -> None:
        self.serial_text.clear()
        self._disp_buf.clear()
        self._disp_buf_chars = 0
        self._display_utf8_bytes = 0
        self.rx_frame_count = 0
        self.tx_frame_count = 0
        self._refresh_status_bar()

    # ================================================================
    # 发送（业务逻辑原样）
    # ================================================================

    def _set_send_mode(self, mode: str) -> None:
        self.send_mode = mode
        self.btn_mode_proto.setChecked(mode == "protocol")
        self.btn_mode_hex.setChecked(mode == "raw_hex")
        self.btn_mode_ascii.setChecked(mode == "raw_ascii")
        self.fields_edit.setVisible(mode == "protocol")
        self.raw_edit.setVisible(mode != "protocol")

    def _encode_current_protocol(self) -> bytes:
        if not self.cfg:
            raise CommandValidationError("请先选择协议")
        cmd_s = (self.tx_cmd_code or "").strip()
        if not cmd_s:
            raise CommandValidationError("请输入命令字 CmdID")
        try:
            if cmd_s.lower().startswith("0x"):
                cmd_code = int(cmd_s, 16)
            else:
                cmd_code = int(cmd_s, 0)
        except (TypeError, ValueError) as exc:
            raise CommandValidationError(
                f"命令字 CmdID 格式不正确：{cmd_s!r}，请输入十进制或 0x 开头的十六进制数"
            ) from exc
        fields_txt = self.fields_edit.toPlainText().strip()
        fields = json.loads(fields_txt) if fields_txt else {}
        direction = "response" if self.tx_direction == "MCU发送" else "request"
        from protocol_parser.parser import encode_frame
        return encode_frame(cmd_code, self.cfg, direction=direction, fields=fields)

    def _stop_tx_cycle(self, message: str | None = None) -> None:
        timer = getattr(self, "_tx_cycle_timer", None)
        if timer is not None:
            timer.stop()
        self.tx_cycle = False
        button = getattr(self, "btn_cycle", None)
        if button is not None:
            button.blockSignals(True)
            button.setChecked(False)
            button.blockSignals(False)
        if message:
            self._enqueue_display_text(
                f"[循环发送已停止] {message}\n", color=PALETTE["error"]
            )
            self._set_status(f"循环发送失败：{message}")

    def _validate_send_input_nonmodal(self) -> None:
        """Highlight invalid HEX after typing/paste without opening a dialog."""
        editor = getattr(self, "raw_edit", None)
        if editor is None:
            return
        if self.send_mode != "raw_hex":
            editor.setStyleSheet(_TEXT_EDIT_FRAME_QSS)
            editor.setToolTip("")
            return
        text = editor.toPlainText().strip()
        if not text:
            editor.setStyleSheet(_TEXT_EDIT_FRAME_QSS)
            editor.setToolTip("")
            return
        try:
            parse_hex_input(text)
        except ProtocolError as exc:
            editor.setStyleSheet(
                _TEXT_EDIT_FRAME_QSS
                + f"\nQTextEdit#SendRawText {{ border: 1px solid {PALETTE['error']}; }}"
            )
            editor.setToolTip(str(exc))
            self._set_status(f"HEX 输入有误：{exc}")
        else:
            editor.setStyleSheet(_TEXT_EDIT_FRAME_QSS)
            editor.setToolTip("")

    def _set_tx_append_crlf(self, checked: bool) -> None:
        """同步发送面板与指令库的 CRLF 开关。

        两个按钮只是同一状态的两个入口；发送面板和指令库仍各自决定
        ASCII/HEX 格式，不互相绑定发送模式。
        """
        value = bool(checked)
        self.tx_append_crlf = value
        for name in ("btn_crlf", "btn_cmdlib_crlf"):
            button = getattr(self, name, None)
            if button is None or button.isChecked() == value:
                continue
            button.blockSignals(True)
            try:
                button.setChecked(value)
            finally:
                button.blockSignals(False)

    def _on_send_once(self) -> bool:
        """发送当前输入。

        用户输入错误（例如 HEX 中输入了非 0-9/A-F 字符）只给出明确提示，
        不再按“未知错误”写 error.log；真正的程序异常仍交给统一错误记录。
        """
        in_cycle = bool(self.tx_cycle or (
            self._tx_cycle_timer is not None and self._tx_cycle_timer.isActive()
        ))
        if not (self.collector and self.collector.running):
            message = "请先打开串口（开始监控）后再发送"
            if in_cycle:
                self._stop_tx_cycle(message)
            else:
                QMessageBox.warning(self, "提示", message)
            return False

        mode = self.send_mode
        try:
            if mode == "protocol":
                data = self._encode_current_protocol()
                self.collector.send(
                    data,
                    metadata={"display_format": "HEX", "send_source": "send_panel_protocol"},
                )
                return True

            if mode == "raw_hex":
                text = self.raw_edit.toPlainText().strip()
                if not text:
                    raise CommandValidationError("请输入 HEX 内容")

                # 复用协议模块的严格 HEX 解析器，兼容空格、换行及合法 0x 前缀。
                payload = parse_hex_input(text)
                if self.tx_auto_crc8:
                    from protocol_parser.parser import calc_checksum
                    payload += calc_checksum(payload, self.tx_crc_algo)
                if self.tx_append_crlf:
                    payload += b"\r\n"
                self.collector.send(
                    payload,
                    metadata={"display_format": "HEX", "send_source": "send_panel"},
                )
                return True

            if mode == "raw_ascii":
                text = self.raw_edit.toPlainText()
                if not text:
                    raise CommandValidationError("请输入 ASCII 内容")
                if self.tx_append_crlf and not text.endswith(("\r\n", "\n")):
                    text += "\r\n"
                self.collector.send_raw(
                    text,
                    as_text=True,
                    metadata={"display_format": "ASCII", "send_source": "send_panel"},
                )
                return True

            raise CommandValidationError(f"未知发送模式：{mode}")

        except json.JSONDecodeError as exc:
            message = f"协议字段必须是合法 JSON。第 {exc.lineno} 行，第 {exc.colno} 列：{exc.msg}"
            if in_cycle:
                self._stop_tx_cycle(message)
            else:
                QMessageBox.warning(self, "协议字段格式错误", message)
        except ProtocolError as exc:
            friendly, debug = classify_protocol_error(exc)
            message = friendly
            if debug and debug != friendly:
                message += f"\n\n原因：{debug}"
            if in_cycle:
                self._stop_tx_cycle(message)
            else:
                QMessageBox.warning(self, "发送内容有误", message)
        except (ValueError, TypeError, UnicodeError, RuntimeError) as exc:
            message = str(exc) or "请检查发送内容。"
            if in_cycle:
                self._stop_tx_cycle(message)
            else:
                QMessageBox.warning(self, "发送内容有误", message)
        except Exception as exc:
            if in_cycle:
                self._stop_tx_cycle(str(exc) or exc.__class__.__name__)
                _log_error_to_disk(exc)
            else:
                self._report_error("发送失败", exc)
        return False

    def _on_clear_send(self) -> None:
        if self.send_mode == "protocol":
            self.fields_edit.clear()
        else:
            self.raw_edit.clear()

    def _on_toggle_cycle_send(self, checked: bool) -> None:
        if not checked:
            self._stop_tx_cycle()
            self._set_status("已停止循环发送")
            return
        if not (self.collector and self.collector.running):
            QMessageBox.warning(self, "提示", "请先打开串口后再发送")
            self.btn_cycle.setChecked(False)
            return
        self.tx_cycle = True
        if not self._on_send_once():
            return
        if self._tx_cycle_timer is None:
            self._tx_cycle_timer = QTimer(self)
            self._tx_cycle_timer.timeout.connect(self._safe(self._on_send_once))
        self._tx_cycle_timer.start(max(10, self.tx_interval_ms))
        self._set_status("循环发送已开始")

    # ================================================================
    # 原始数据保存（业务逻辑原样）
    # ================================================================

    def _refresh_save_raw_button(self) -> None:
        """同步存储按钮文字与视觉状态。"""
        if self.save_raw_enabled:
            self.btn_save_raw.setText("✓ 停止存储数据")
            self.btn_save_raw.setStyleSheet(_STORAGE_ACTIVE_QSS)
        else:
            self.btn_save_raw.setText("开始存储数据")
            # 恢复 qfluentwidgets 原始样式，避免一次开关后退化为系统原生按钮。
            self.btn_save_raw.setStyleSheet(getattr(self, "_save_raw_default_qss", ""))
            self.btn_save_raw.style().unpolish(self.btn_save_raw)
            self.btn_save_raw.style().polish(self.btn_save_raw)
            self.btn_save_raw.update()

    def _set_storage_format_controls_enabled(self, enabled: bool) -> None:
        button = getattr(self, "btn_hex", None)
        if button is None:
            return
        button.setEnabled(bool(enabled))
        if enabled:
            apply_tooltip(button, "蓝色：HEX格式；白色：ASCII格式")
        else:
            apply_tooltip(button, "原始数据存储期间显示/写入格式已锁定；停止存储后可切换")

    def _toggle_save_raw(self) -> None:
        self.save_raw_enabled = not self.save_raw_enabled
        self._refresh_save_raw_button()
        if self.save_raw_enabled:
            if self.is_collecting and not self._save_raw_active:
                self._open_save_raw_file()
        else:
            self._close_save_raw_file()
        self._set_status("")

    def _choose_save_raw_path(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "选择保存路径")
        if path:
            self.save_raw_path = path
            self.save_path_edit.setText(path)

    def _open_receive_file_location(self) -> None:
        """打开当前接收文件所在文件夹；未存储时打开当前保存路径。"""
        folder: Path | None = None

        # 存储进行中时，优先使用后台写入器的当前文件目录。
        writer = self._raw_writer
        if writer is not None and writer.current_path is not None:
            try:
                folder = writer.current_path.expanduser().resolve().parent
            except OSError:
                folder = writer.current_path.parent

        # 尚未创建文件时，使用界面中的当前保存路径。
        if folder is None:
            raw_path = self.save_path_edit.text().strip() or self.save_raw_path
            folder = Path(raw_path).expanduser()
            try:
                folder = folder.resolve()
            except Exception:
                pass

        try:
            folder.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            raise StorageOperationError(f"无法创建或访问接收文件目录：{folder}") from exc

        opened = QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
        if not opened:
            raise StorageOperationError(f"无法打开接收文件目录：{folder}")

    def _write_raw_data(self, data: bytes, ts: float, prefix: str = "") -> None:
        writer = self._raw_writer
        if not self._save_raw_active or writer is None:
            return
        accepted = writer.enqueue(data, ts, prefix)
        if not accepted and writer.running:
            # The writer callback reports the exact cumulative drop count.
            self._update_stats_bar()

    @Slot(str)
    def _on_storage_error(self, message: str) -> None:
        self._save_raw_active = False
        self.save_raw_enabled = False
        self._refresh_save_raw_button()
        self._set_storage_format_controls_enabled(True)
        self._set_status(message)
        QMessageBox.warning(self, "原始数据保存提示", message)

    @Slot(int)
    def _on_storage_drop(self, dropped: int) -> None:
        self._raw_writer_drop_count = int(dropped)
        self._set_status(
            f"原始数据写入速度不足，已丢弃 {dropped} 条记录；请降低接收速率或更换更快的磁盘"
        )

    def _open_save_raw_file(self) -> None:
        if self._save_raw_active and self._raw_writer is not None:
            return
        save_dir = Path(self.save_path_edit.text().strip() or self.save_raw_path).expanduser()
        filename = self.save_name_edit.text().strip() or "serial_data"
        self.save_raw_path = str(save_dir)
        self.save_raw_filename = filename
        writer = RawDataWriter(
            directory=save_dir,
            basename=filename,
            max_file_bytes=self.save_raw_max_size,
            ascii_mode=not self.hex_format,
            queue_size=5000,
            on_error=lambda message: self.bridge.storage_error_signal.emit(message),
            on_drop=lambda count: self.bridge.storage_drop_signal.emit(count),
        )
        try:
            path = writer.start()
        except StorageOperationError as exc:
            self._raw_writer = None
            self._save_raw_active = False
            self.save_raw_enabled = False
            self._refresh_save_raw_button()
            self._report_error("原始数据保存失败", exc)
            return
        self._raw_writer = writer
        self._save_raw_active = True
        self._set_storage_format_controls_enabled(False)
        self._raw_writer_drop_count = 0
        self.save_raw_file = None
        self._set_status(f"正在保存原始数据: {path}")

    def _rotate_save_raw_file(self) -> None:
        # Rotation is owned by RawDataWriter to avoid cross-thread file races.
        return

    def _close_save_raw_file(self) -> None:
        self._save_raw_active = False
        self._set_storage_format_controls_enabled(True)
        writer = self._raw_writer
        self._raw_writer = None
        if writer is None:
            return
        try:
            stats = writer.stop(drain=True, timeout=5.0)
            self.save_raw_current_size = stats.written_bytes
            self.save_raw_count = stats.file_index
            self._raw_writer_drop_count = stats.dropped_records
            if stats.dropped_records:
                self._set_status(
                    f"原始数据保存已停止，共丢弃 {stats.dropped_records} 条记录"
                )
        except StorageOperationError as exc:
            self.bridge.storage_error_signal.emit(str(exc))

    # ================================================================
    # 指令库（简化但接口兼容）
    # ================================================================

    def _cmdlib_initialize_rows(self) -> None:
        """只创建一次固定 40 行的单元格和发送按钮。"""
        if getattr(self, "_cmdlib_send_buttons", None):
            return
        self._cmdlib_send_buttons: list[PushButton] = []
        self.cmdlib_table.blockSignals(True)
        self.cmdlib_table.setUpdatesEnabled(False)
        try:
            self.cmdlib_table.setRowCount(self.CMDLIB_MAX)
            for row in range(self.CMDLIB_MAX):
                name_item = QTableWidgetItem("")
                payload_item = QTableWidgetItem("")
                item_font = QFont(
                    getattr(self, "_cmdlib_table_font", _make_crisp_ui_font(_CMDLIB_FONT_BASE_SIZE))
                )
                name_item.setFont(item_font)
                payload_item.setFont(item_font)
                self.cmdlib_table.setItem(row, 0, name_item)
                self.cmdlib_table.setItem(row, 1, payload_item)

                send_button = PushButton("发送")
                send_button.setProperty("commandTableButton", True)
                send_button.setContentsMargins(0, 0, 0, 0)
                button_font = QFont(
                    getattr(self, "_cmdlib_table_font", _make_crisp_ui_font(_CMDLIB_FONT_BASE_SIZE))
                )
                send_button.setFont(button_font)
                send_width, send_height = _fit_button_to_text(
                    send_button,
                    horizontal_padding=24,
                    vertical_padding=8,
                    minimum_width=_CMDLIB_SEND_BUTTON_MIN_WIDTH,
                    minimum_height=_CMDLIB_SEND_BUTTON_MIN_HEIGHT,
                )
                send_button.setMaximumSize(16_777_215, 16_777_215)
                send_button.setEnabled(False)
                send_button.clicked.connect(
                    self._safe(lambda checked=False, idx=row: self._cmdlib_send_by_index(idx))
                )
                self._cmdlib_send_buttons.append(send_button)

                action_cell = QWidget(self.cmdlib_table)
                action_layout = QHBoxLayout(action_cell)
                action_layout.setContentsMargins(3, 3, 3, 3)
                action_layout.setSpacing(0)
                action_layout.addStretch(1)
                action_layout.addWidget(send_button)
                action_layout.addStretch(1)
                self.cmdlib_table.setCellWidget(row, 2, action_cell)
                row_height = max(
                    getattr(self, "_cmdlib_row_height", 32),
                    send_height + 6,
                )
                self.cmdlib_table.setRowHeight(row, row_height)

            if self._cmdlib_send_buttons:
                widest = max(button.minimumWidth() for button in self._cmdlib_send_buttons)
                self.cmdlib_table.setColumnWidth(
                    2, max(_CMDLIB_ACTION_COLUMN_WIDTH, widest + 16)
                )
        finally:
            self.cmdlib_table.blockSignals(False)
            self.cmdlib_table.setUpdatesEnabled(True)

    def _cmdlib_path(self, name: str) -> Path:
        return user_data_path("cmdlib") / f"{name}.json"

    def _cmdlib_load(self, force: bool = False) -> None:
        if getattr(self, "_cmdlib_loaded", False) and not force:
            return

        removed_generated: dict[str, bool] = {}

        def _load(name: str, command_type: str) -> list[dict]:
            p = self._cmdlib_path(name)
            removed_generated[name] = False
            try:
                if p.exists():
                    data = json.loads(p.read_text(encoding="utf-8"))
                    if isinstance(data, list):
                        normalized: list[dict] = []
                        for raw in data[: self.CMDLIB_MAX]:
                            if not isinstance(raw, dict):
                                continue
                            # 旧版本由 Word/产品 JSON 自动写入的记录带有
                            # generated 标记。迁移时清理，确保指令库只剩
                            # 用户手工录入内容。
                            if bool(raw.get("generated")):
                                removed_generated[name] = True
                                continue
                            item = dict(raw)
                            item.pop("generated", None)
                            item["name"] = str(item.get("name") or "")
                            item["payload"] = str(
                                item.get("payload") or item.get("data") or ""
                            )
                            item["type"] = command_type
                            normalized.append(item)
                        return normalized
            except Exception:
                pass
            return []

        self._cmdlib_hex = _load("hex_cmds", "HEX")
        self._cmdlib_ascii = _load("ascii_cmds", "ASCII")
        self._cmdlib_cycle_hex = _load("cycle_hex", "HEX")
        self._cmdlib_cycle_ascii = _load("cycle_ascii", "ASCII")

        # 清理循环配置中对旧自动生成记录的悬空引用。
        valid_hex_ids = {str(item.get("id") or "") for item in self._cmdlib_hex}
        valid_ascii_ids = {str(item.get("id") or "") for item in self._cmdlib_ascii}
        old_cycle_hex = list(self._cmdlib_cycle_hex)
        old_cycle_ascii = list(self._cmdlib_cycle_ascii)
        self._cmdlib_cycle_hex = [
            item for item in old_cycle_hex
            if str(item.get("id") or "") in valid_hex_ids
        ]
        self._cmdlib_cycle_ascii = [
            item for item in old_cycle_ascii
            if str(item.get("id") or "") in valid_ascii_ids
        ]

        self._cmdlib_loaded = True
        if removed_generated.get("hex_cmds"):
            self._cmdlib_save_list("hex_cmds", self._cmdlib_hex)
        if removed_generated.get("ascii_cmds"):
            self._cmdlib_save_list("ascii_cmds", self._cmdlib_ascii)
        if old_cycle_hex != self._cmdlib_cycle_hex:
            self._cmdlib_save_list("cycle_hex", self._cmdlib_cycle_hex)
        if old_cycle_ascii != self._cmdlib_cycle_ascii:
            self._cmdlib_save_list("cycle_ascii", self._cmdlib_cycle_ascii)
        self._cmdlib_refresh_list()

    def _cmdlib_save_list(self, name: str, items: list) -> None:
        # 编辑完成后的短时间内可能连续触发多个 itemChanged，合并为一次磁盘写入。
        self._cmdlib_pending_save[name] = [dict(item) for item in items]
        self._cmdlib_save_timer.start()

    def _cmdlib_flush_pending_save(self) -> None:
        pending = self._cmdlib_pending_save
        if not pending:
            return
        self._cmdlib_pending_save = {}
        failed: dict[str, list] = {}
        for name, items in pending.items():
            try:
                p = self._cmdlib_path(name)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(
                    json.dumps(items, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as exc:
                failed[name] = items
                try:
                    _log_error_to_disk(exc)
                except Exception:
                    pass
        if failed:
            # Keep unsaved edits for a later retry without letting a disk
            # failure abort serial shutdown or crash a QTimer callback.
            self._cmdlib_pending_save.update(failed)
            self._set_status("指令库保存失败，编辑内容已保留待重试")

    def _cmdlib_current_list(self) -> list[dict]:
        return self._cmdlib_hex if self._cmdlib_mode == "hex" else self._cmdlib_ascii

    def _cmdlib_set_current_list(self, items: list[dict]) -> None:
        command_type = "HEX" if self._cmdlib_mode == "hex" else "ASCII"
        normalized: list[dict] = []
        for raw in items[: self.CMDLIB_MAX]:
            item = dict(raw) if isinstance(raw, dict) else {}
            item["name"] = str(item.get("name") or "")
            item["payload"] = str(item.get("payload") or item.get("data") or "")
            item["type"] = command_type
            normalized.append(item)

        # 只裁掉末尾完全空白的记录；中间空行保留，确保可在任意行录入。
        while normalized and not (
            normalized[-1].get("name", "").strip()
            or normalized[-1].get("payload", "").strip()
        ):
            normalized.pop()

        if self._cmdlib_mode == "hex":
            self._cmdlib_hex = normalized
            self._cmdlib_save_list("hex_cmds", normalized)
        else:
            self._cmdlib_ascii = normalized
            self._cmdlib_save_list("ascii_cmds", normalized)

    def _cmdlib_refresh_list(self) -> None:
        if not hasattr(self, "cmdlib_table"):
            return

        self._cmdlib_initialize_rows()
        items = self._cmdlib_current_list()
        self._cmdlib_refreshing = True
        self.cmdlib_table.blockSignals(True)
        self.cmdlib_table.setUpdatesEnabled(False)
        try:
            for row in range(self.CMDLIB_MAX):
                item = items[row] if row < len(items) else {}
                name = str(item.get("name") or "")
                payload = str(item.get("payload") or item.get("data") or "")
                name_item = self.cmdlib_table.item(row, 0)
                payload_item = self.cmdlib_table.item(row, 1)
                if name_item is None:
                    name_item = QTableWidgetItem("")
                    self.cmdlib_table.setItem(row, 0, name_item)
                if payload_item is None:
                    payload_item = QTableWidgetItem("")
                    self.cmdlib_table.setItem(row, 1, payload_item)
                current_table_font = QFont(
                    getattr(self, "_cmdlib_table_font", _make_crisp_ui_font(_CMDLIB_FONT_BASE_SIZE))
                )
                name_item.setFont(current_table_font)
                payload_item.setFont(current_table_font)
                if name_item.text() != name:
                    name_item.setText(name)
                if payload_item.text() != payload:
                    payload_item.setText(payload)
                self._cmdlib_send_buttons[row].setFont(current_table_font)
                self._cmdlib_send_buttons[row].setEnabled(bool(payload.strip()))
        finally:
            self.cmdlib_table.blockSignals(False)
            self.cmdlib_table.setUpdatesEnabled(True)
            self.cmdlib_table.viewport().update()
            self._cmdlib_refreshing = False

    def _cmdlib_send_by_index(self, idx: int) -> None:
        items = self._cmdlib_current_list()
        if idx >= len(items):
            return
        self._cmdlib_send_one(items[idx])

    def _cmdlib_send_one(self, item: dict, *, suppress_modal: bool = False) -> bool:
        if not (self.collector and self.collector.running):
            message = "请先开始监控"
            if suppress_modal:
                self._set_status(message)
            else:
                QMessageBox.warning(self, "提示", message)
            return False

        payload = str(item.get("payload") or item.get("data") or "").strip()
        if not payload:
            return False

        # 发送格式跟随当前指令记录本身；旧数据没有 type 时才回退到当前库模式。
        command_type = str(
            item.get("type") or ("HEX" if self._cmdlib_mode == "hex" else "ASCII")
        ).strip().upper()

        try:
            if command_type == "HEX":
                s = payload.replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")
                if s.lower().startswith("0x"):
                    s = s[2:]
                if not s or len(s) % 2:
                    raise CommandValidationError("HEX 长度必须为偶数")
                try:
                    data = bytes.fromhex(s)
                except ValueError as exc:
                    raise CommandValidationError(
                        "HEX 指令包含非法字符，只允许 0-9、A-F 和空白分隔符"
                    ) from exc
                if self.tx_auto_crc8:
                    from protocol_parser.parser import calc_checksum
                    cs = calc_checksum(data, self.tx_crc_algo)
                    if cs:
                        data = data + cs
                if self.tx_append_crlf:
                    data = data + b"\r\n"
                self.collector.send(
                    data,
                    metadata={"display_format": "HEX", "send_source": "command_library"},
                )
            elif command_type == "ASCII":
                text = payload
                if self.tx_append_crlf and not text.endswith("\r\n") and not text.endswith("\n"):
                    text = text + "\r\n"
                self.collector.send_raw(
                    text,
                    as_text=True,
                    metadata={"display_format": "ASCII", "send_source": "command_library"},
                )
            else:
                raise CommandValidationError(f"不支持的指令类型：{command_type}")

            self._set_status(f"指令库已发送: {item.get('name', '')}")
            self._update_stats_bar()
            return True
        except Exception as e:
            if suppress_modal:
                try:
                    _log_error_to_disk(e)
                except Exception:
                    pass
                self._enqueue_display_text(
                    f"[指令库循环已停止] {e}\n", color=PALETTE["error"]
                )
                self._set_status(f"指令库循环发送失败：{e}")
            else:
                self._report_error("指令库发送失败", e)
            return False

    def _cmdlib_toggle_mode(self, checked: bool) -> None:
        # 按钮文字始终为 HEX：蓝色选中表示 HEX，未选中表示 ASCII。
        self._cmdlib_mode = "hex" if checked else "ascii"
        self.btn_cmdlib_mode.setText("HEX")
        self._cmdlib_refresh_list()

    def _cmdlib_on_item_changed(self, changed_item: QTableWidgetItem) -> None:
        """保存名称或指令数据的直接编辑结果。"""
        if getattr(self, "_cmdlib_refreshing", False):
            return

        row = changed_item.row()
        column = changed_item.column()
        if row < 0 or row >= self.CMDLIB_MAX or column not in (0, 1):
            return

        items = [dict(x) for x in self._cmdlib_current_list()]
        while len(items) <= row:
            items.append({"id": "", "name": "", "payload": ""})

        name_item = self.cmdlib_table.item(row, 0)
        payload_item = self.cmdlib_table.item(row, 1)
        name = name_item.text().strip() if name_item else ""
        payload = payload_item.text().strip() if payload_item else ""

        entry = items[row]
        entry["name"] = name
        entry["payload"] = payload
        entry["type"] = "HEX" if self._cmdlib_mode == "hex" else "ASCII"
        if (name or payload) and not entry.get("id"):
            import uuid
            entry["id"] = uuid.uuid4().hex
        if not name and not payload:
            entry["id"] = ""

        self._cmdlib_set_current_list(items)

        if 0 <= row < len(getattr(self, "_cmdlib_send_buttons", [])):
            self._cmdlib_send_buttons[row].setEnabled(bool(payload))

    def _cmdlib_open_cycle_config(self) -> None:
        """配置循环发送：勾选 + 间隔 + 手动排序。"""
        items = [it for it in self._cmdlib_current_list() if it.get("id")]
        if not items:
            QMessageBox.information(self, "提示", "当前指令库为空，请先在空白行录入指令")
            return
        is_hex = self._cmdlib_mode == "hex"
        seq = list(self._cmdlib_cycle_hex if is_hex else self._cmdlib_cycle_ascii)
        dlg = CycleConfigDialog(self, items, seq, is_hex)
        if dlg.exec() == QDialog.Accepted and dlg.result_seq is not None:
            if is_hex:
                self._cmdlib_cycle_hex = dlg.result_seq
                self._cmdlib_save_list("cycle_hex", dlg.result_seq)
            else:
                self._cmdlib_cycle_ascii = dlg.result_seq
                self._cmdlib_save_list("cycle_ascii", dlg.result_seq)
            self._set_status(f"循环配置已保存（{len(dlg.result_seq)} 条）")

    def _cmdlib_toggle_cycle(self) -> None:
        if self._cmdlib_cycle_on:
            self._cmdlib_stop_cycle()
            return
        seq = self._cmdlib_cycle_hex if self._cmdlib_mode == "hex" else self._cmdlib_cycle_ascii
        if not seq:
            QMessageBox.information(self, "提示", "请先配置循环指令")
            return
        if not (self.collector and self.collector.running):
            QMessageBox.warning(self, "提示", "请先开始监控")
            return
        self._cmdlib_cycle_on = True
        self._cmdlib_cycle_idx = 0
        self.btn_cmdlib_cycle.setText("停止循环")
        self._cmdlib_cycle_tick()

    def _cmdlib_stop_cycle(self) -> None:
        self._cmdlib_cycle_on = False
        if self._cmdlib_cycle_timer:
            self._cmdlib_cycle_timer.stop()
        self.btn_cmdlib_cycle.setText("循环发送")

    def _cmdlib_cycle_tick(self) -> None:
        if not self._cmdlib_cycle_on:
            return
        seq = self._cmdlib_cycle_hex if self._cmdlib_mode == "hex" else self._cmdlib_cycle_ascii
        if not seq:
            self._cmdlib_stop_cycle()
            return
        step = seq[self._cmdlib_cycle_idx % len(seq)]
        pool = {x.get("id"): x for x in self._cmdlib_current_list()}
        item = pool.get(step.get("id"))
        if item and not self._cmdlib_send_one(item, suppress_modal=True):
            self._cmdlib_stop_cycle()
            return
        delay = max(10, int(step.get("delay_ms", 1000) or 1000))
        self._cmdlib_cycle_idx = (self._cmdlib_cycle_idx + 1) % len(seq)
        if self._cmdlib_cycle_timer is None:
            self._cmdlib_cycle_timer = QTimer(self)
            self._cmdlib_cycle_timer.setSingleShot(True)
            self._cmdlib_cycle_timer.timeout.connect(self._cmdlib_cycle_tick)
        self._cmdlib_cycle_timer.start(delay)

    # ================================================================
    # 实时属性 / 预置命令 / 角色扩展
    # ================================================================





    @staticmethod
    def _readonly_item(text: object) -> QTableWidgetItem:
        item = QTableWidgetItem(str(text))
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        return item




    def _next_msg_id(self) -> int:
        self._message_id = (int(self._message_id) % 255) + 1
        return self._message_id

    def _require_open_collector(self) -> SerialCollector | None:
        if not (self.collector and self.collector.running):
            QMessageBox.warning(self, "提示", "请先开始监控")
            return None
        if not self.cfg:
            QMessageBox.warning(self, "提示", "请先加载产品协议")
            return None
        return self.collector

    def _send_generated_frame(self, frame: bytes, label: str = "预置命令") -> bool:
        collector = self._require_open_collector()
        if collector is None:
            return False
        collector.send(frame)
        self._set_status(f"已发送：{label}")
        return True


















    # ================================================================
    # 其他 UI 事件
    # ================================================================



    def _on_more_config_toggled(self, checked: bool) -> None:
        """展开/收起串口详细参数与原始数据存储区域。"""
        panel = getattr(self, "serial_detail_panel", None)
        if panel is not None:
            panel.setVisible(bool(checked))
        self.btn_more_config.setText("收起 ▲" if checked else "更多 ▼")
        self.serial_config_card.updateGeometry()

    def _on_hex_toggled(self, checked: bool) -> None:
        self.hex_format = checked
        # 按钮文字固定为 HEX格式：蓝色选中表示 HEX，未选中表示 ASCII。
        self.btn_hex.setText("HEX格式")
        if not checked:
            self.view_mode = "raw"
            self.btn_view_mode.setChecked(False)
            self.btn_view_mode.setEnabled(False)
        else:
            self.btn_view_mode.setEnabled(True)
        if self.collector:
            self.collector.raw_mode = (not checked) or (self.view_mode == "raw")
            direction = None
            if checked:
                direction = "request" if self.serial_sender == "模组发送" else "response"
            self.collector.direction = direction

    def _on_view_mode_toggled(self, checked: bool) -> None:
        self.view_mode = "protocol" if checked else "raw"
        self.btn_view_mode.setText("协议解析模式" if checked else "原始数据模式")

        # 协议解析未开启时，隐藏“模组发送/MCU发送”到“查看协议”的整组控件。
        protocol_controls = getattr(self, "protocol_controls_container", None)
        if protocol_controls is not None:
            protocol_controls.setVisible(checked)

        # 协议工具组显隐会改变左侧最小尺寸，立即重平衡分栏，防止左右控件显示不全。
        self._schedule_splitter_rebalance()

        if self.collector:
            self.collector.raw_mode = (not self.hex_format) or (self.view_mode == "raw")

    def _on_sender_changed(self, value: str) -> None:
        self.serial_sender = "MCU发送" if str(value) == "MCU发送" else "模组发送"
        if self.collector and self.hex_format:
            self.collector.direction = (
                "request" if self.serial_sender == "模组发送" else "response"
            )

    def _on_topmost_toggled(self, checked: bool) -> None:
        """切换置顶；Windows 使用 SetWindowPos，避免 setWindowFlag()+show() 闪屏。"""
        if sys.platform.startswith("win"):
            try:
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                set_window_pos = user32.SetWindowPos
                set_window_pos.argtypes = (
                    wintypes.HWND, wintypes.HWND,
                    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                    wintypes.UINT,
                )
                set_window_pos.restype = wintypes.BOOL

                hwnd = wintypes.HWND(int(self.winId()))
                insert_after = wintypes.HWND(-1 if checked else -2)
                flags = 0x0001 | 0x0002 | 0x0010 | 0x0200  # NOSIZE|NOMOVE|NOACTIVATE|NOOWNERZORDER
                if not set_window_pos(hwnd, insert_after, 0, 0, 0, 0, flags):
                    raise ctypes.WinError(ctypes.get_last_error())
                self._set_status("窗口已置顶" if checked else "已取消置顶")
                return
            except Exception as exc:
                self.btn_topmost.blockSignals(True)
                self.btn_topmost.setChecked(not checked)
                self.btn_topmost.blockSignals(False)
                QMessageBox.warning(self, "置顶失败", f"无法修改窗口置顶状态：{exc}")
                return

        # 非 Windows 平台保留 Qt 回退逻辑。
        self.setWindowFlag(Qt.WindowStaysOnTopHint, checked)
        self.show()

    def _add_serial_port(self) -> None:
        ports = SerialCollector.list_ports()
        if not ports:
            QMessageBox.warning(self, "提示", "未找到可用串口")
            return

        dlg = AddSerialPortDialog(self, ports)
        if dlg.exec() == QDialog.Accepted:
            self._spawn_monitor(dlg.selected_port(), dlg.selected_baud())

    def _spawn_monitor(self, port: str, baudrate: int) -> None:
        import subprocess
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--monitor", port, str(int(baudrate))]
            DETACHED_PROCESS = 0x00000008
            try:
                subprocess.Popen(cmd, creationflags=DETACHED_PROCESS, close_fds=True)
            except Exception as e:
                self._report_error("启动新串口窗口失败", e)
        else:
            script_path = Path(__file__).resolve()
            try:
                subprocess.Popen([sys.executable, str(script_path), "--monitor", port, str(int(baudrate))], close_fds=True)
            except Exception as e:
                self._report_error("启动新串口窗口失败", e)

    def _apply_monitor_args(self) -> None:
        self._refresh_ports()
        if self._monitor_port:
            for i in range(self.port_combo.count()):
                text = self.port_combo.itemText(i)
                if text.startswith(self._monitor_port):
                    self.port_combo.setCurrentIndex(i)
                    break
        self.baud_combo.setCurrentText(str(self._monitor_baud))
        self.baudrate_var = str(self._monitor_baud)
        self.baudrate_var_last_valid = str(self._monitor_baud)
        self.setWindowTitle(f"{APP_NAME} v{VERSION} - {self._monitor_port} @ {self._monitor_baud}")

    def _choose_log(self) -> None:
        content = self.serial_text.toPlainText()
        if not content.strip():
            QMessageBox.information(self, "保存日志", "当前实时数据为空")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "保存日志",
            f"protocol_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log",
            "日志文件 (*.log);;文本文件 (*.txt)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(f"===== 导出时间 {datetime.now().isoformat(timespec='seconds')} =====\n")
                f.write(content)
                if not content.endswith("\n"):
                    f.write("\n")
            self._set_status(f"日志已保存: {path}")
            QMessageBox.information(self, "保存日志", f"已保存到:\n{path}")
        except Exception as e:
            self._report_error("日志保存失败", e)

    def closeEvent(self, event) -> None:
        try:
            self._cmdlib_flush_pending_save()
        except Exception as exc:
            _log_error_to_disk(exc)
        try:
            self._serial_manual_stop = True
            self._cancel_serial_reconnect()
            # Normal button stops are asynchronous.  Process shutdown is the
            # one place where we wait boundedly so no hidden RX/TX worker or
            # unwritten raw-data queue is abandoned after the window vanishes.
            collectors = []
            for collector in (self.collector, self._stopping_collector):
                if collector is not None and collector not in collectors:
                    collectors.append(collector)
            self.collector = None
            self._stopping_collector = None
            self.is_collecting = False
            self._auto_reply.set_collector(None)
            for collector in collectors:
                try:
                    collector.stop(timeout=3.0)
                except Exception as exc:
                    _log_error_to_disk(exc)
            try:
                self._close_save_raw_file()
            except Exception as exc:
                _log_error_to_disk(exc)
            if self.log_file:
                try:
                    self.log_file.flush()
                    self.log_file.close()
                except Exception as exc:
                    _log_error_to_disk(exc)
        finally:
            try:
                super().closeEvent(event)
            except Exception as exc:
                _log_error_to_disk(exc)
            event.accept()



# ---------- 启动 ----------

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--monitor", nargs=2, metavar=("PORT", "BAUD"), default=None)
    args, _ = ap.parse_known_args()

    monitor_port = None
    monitor_baud = 9600
    if args.monitor is not None:
        monitor_port = args.monitor[0]
        try:
            monitor_baud = int(args.monitor[1])
        except Exception:
            monitor_baud = 9600

    # ---------- 高 DPI（尽量在创建 QApplication 前设置） ----------
    # 优先用环境变量（Qt6 推荐方式），避免 SetProcessDpiAwareness 的「拒绝访问」
    if sys.platform == "win32":
        os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
        os.environ.setdefault("QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough")
        try:
            # 让源码运行和打包后的任务栏/窗口都使用本项目图标，而不是 Python 默认图标。
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                APP_ID
            )
        except Exception:
            pass
        try:
            from ctypes import windll
            # Windows 10/11 首选 PER_MONITOR_AWARE_V2。旧 API 的数值 2
            # 只是 Per-Monitor v1，跨屏后更容易出现字体已变化但控件仍保留
            # 旧尺寸的问题。
            try:
                windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
            except Exception:
                try:
                    windll.shcore.SetProcessDpiAwareness(2)
                except Exception:
                    try:
                        windll.user32.SetProcessDPIAware()
                    except Exception:
                        pass
        except Exception:
            pass

    try:
        QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
            Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
        )
    except Exception:
        pass

    try:
        # 1. 必须先创建 QApplication，再创建任何 QWidget / FluentWindow
        app = QApplication(sys.argv)
        app.setStyle("Fusion")

        # The application font is applied after the Fluent theme is selected so
        # its DPI/resolution-aware point size is not overwritten by theme setup.

        icon_path = resource_path("resources/lkl.ico")
        if not icon_path.exists():
            icon_path = resource_path("resources/lkl.png")
        if icon_path.exists():
            app.setWindowIcon(QIcon(str(icon_path)))

        # 2. 主题必须在创建窗口之前设置（qfluentwidgets 官方推荐顺序）
        setTheme(Theme.LIGHT)
        setThemeColor(PALETTE["primary"])
        apply_application_font(screen=app.primaryScreen())
        install_adaptive_ui_controller(app)

        # 3. 再创建主窗口
        window = ProtocolParserApp(monitor_port=monitor_port, monitor_baud=monitor_baud)
        if icon_path.exists():
            window.setWindowIcon(QIcon(str(icon_path)))
        window.show()
        return app.exec()
    except BaseException as e:
        log_path = _write_crash_log_gui(e)
        try:
            friendly, _ = classify_protocol_error(e)
            print(f"[启动失败] {friendly}", file=sys.stderr)
            if log_path:
                print(f"           详细日志: {log_path}", file=sys.stderr)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
