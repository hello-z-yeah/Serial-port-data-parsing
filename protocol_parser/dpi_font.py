"""DPI-safe fonts and adaptive geometry for the Qt user interface.

Qt 6 point-size fonts already follow Windows per-monitor DPI.  The helpers in
this module therefore keep a stable logical font size and recompute control
geometry from the *actual* font metrics.  The important rule is that text
controls may grow with their caption; they must never retain a stale fixed
height/width from another DPI, theme or monitor.
"""
from __future__ import annotations

import html
import re
import weakref

from PySide6.QtCore import QSize, Qt, QRect, QObject, QEvent, QTimer
from PySide6.QtGui import QFont, QFontMetrics, QScreen
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QTableWidget,
    QAbstractButton,
    QLineEdit,
    QComboBox,
    QAbstractSpinBox,
    QLabel,
    QTabBar,
    QDialog,
    QSizePolicy,
    QHeaderView,
    QFormLayout,
    QLayout,
    QScrollArea,
)

UI_FONT_FAMILY = "Microsoft YaHei UI"
UI_FONT_BASE_POINT_SIZE = 10
UI_FONT_MAX_POINT_SIZE = 14
_QT_MAX_SIZE = 16_777_215
_ADAPT_DEBOUNCE_MS = 120
_CONTROLLER = None


def _screen_for(widget: QWidget | None = None, screen: QScreen | None = None):
    if screen is not None:
        return screen
    if widget is not None:
        try:
            resolved = widget.screen()
            if resolved is not None:
                return resolved
        except Exception:
            pass
    app = QApplication.instance()
    return app.primaryScreen() if app is not None else None


def effective_resolution_scale(
    widget: QWidget | None = None,
    *,
    screen: QScreen | None = None,
) -> float:
    """Return the UI font multiplier.

    Always ``1.0``: Qt maps point-size fonts and logical pixels to the current
    monitor.  Multiplying again from 2K/4K resolution double-scales text while
    leaving several custom Fluent controls at their old logical size.
    """
    del widget, screen
    return 1.0


def responsive_point_size(
    widget: QWidget | None = None,
    *,
    screen: QScreen | None = None,
    base: int = UI_FONT_BASE_POINT_SIZE,
    maximum: int = UI_FONT_MAX_POINT_SIZE,
) -> int:
    del widget, screen
    return max(1, min(int(maximum), int(base)))


def make_ui_font(point_size: int, *, weight: QFont.Weight | None = None) -> QFont:
    font = QFont(UI_FONT_FAMILY)
    font.setPointSize(max(1, int(point_size)))
    if weight is not None:
        font.setWeight(weight)
    try:
        font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    except Exception:
        pass
    try:
        font.setStyleStrategy(
            QFont.StyleStrategy.PreferAntialias
            | QFont.StyleStrategy.PreferQuality
        )
    except Exception:
        pass
    return font


def apply_application_font(
    widget: QWidget | None = None,
    *,
    screen: QScreen | None = None,
) -> int:
    """Apply the common UI font without overriding independent data fonts."""
    app = QApplication.instance()
    point_size = responsive_point_size(widget, screen=screen)
    if app is None:
        return point_size

    signature = f"{UI_FONT_FAMILY}|{point_size}"
    if app.property("_smst_application_font_signature") == signature:
        return point_size

    app.setProperty("_smst_application_font_signature", signature)
    app.setFont(make_ui_font(point_size))

    base_qss = app.property("_smst_application_font_base_qss")
    if base_qss is None:
        base_qss = app.styleSheet()
        app.setProperty("_smst_application_font_base_qss", base_qss)
    app.setStyleSheet(
        str(base_qss or "")
        + f'''\nQWidget {{
            font-family: "{UI_FONT_FAMILY}";
            font-size: {point_size}pt;
        }}\n'''
    )
    return point_size


def _remember_base_constraints(widget: QWidget) -> tuple[int, int, int, int]:
    """Remember design-time constraints so repeated passes can also shrink."""
    names = (
        "_smst_base_min_w",
        "_smst_base_min_h",
        "_smst_base_max_w",
        "_smst_base_max_h",
    )
    current = (
        int(widget.minimumWidth()),
        int(widget.minimumHeight()),
        int(widget.maximumWidth()),
        int(widget.maximumHeight()),
    )
    values: list[int] = []
    for name, value in zip(names, current):
        stored = widget.property(name)
        if stored is None:
            widget.setProperty(name, value)
            stored = value
        try:
            values.append(int(stored))
        except Exception:
            values.append(value)
    return tuple(values)  # type: ignore[return-value]


def _visible_button_text(button: QWidget) -> str:
    getter = getattr(button, "text", None)
    if not callable(getter):
        return ""
    try:
        return str(getter() or "").replace("&&", "\0").replace("&", "").replace("\0", "&")
    except Exception:
        return ""


def _plain_label_text(label: QLabel) -> str:
    try:
        text = str(label.text() or "")
    except Exception:
        return ""
    # Status labels may use rich text.  Metrics must measure the visible text,
    # not the HTML markup itself.
    text = re.sub(r"<br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).replace("\n", " ").strip()


def _is_combo_like(widget: QWidget) -> bool:
    if isinstance(widget, QComboBox):
        return True
    name = type(widget).__name__.lower()
    return "combobox" in name and all(
        hasattr(widget, attr) for attr in ("count", "itemText", "currentText")
    )


def _is_spin_like(widget: QWidget) -> bool:
    if isinstance(widget, QAbstractSpinBox):
        return True
    name = type(widget).__name__.lower()
    return name.endswith("spinbox") and hasattr(widget, "lineEdit")


def _clear_stale_fixed_limits(
    widget: QWidget,
    *,
    required_width: int,
    required_height: int,
    clear_width: bool,
    clear_height: bool,
) -> None:
    """Release old finite maxima that would crop the current caption."""
    if bool(widget.property("smstKeepFixedGeometry")):
        return
    if clear_width and widget.maximumWidth() < required_width:
        widget.setMaximumWidth(_QT_MAX_SIZE)
    if clear_height and widget.maximumHeight() < required_height:
        widget.setMaximumHeight(_QT_MAX_SIZE)


def _set_full_text_tooltip(widget: QWidget, text: str) -> None:
    if not text or bool(widget.property("smstNoAutoToolTip")):
        return
    try:
        current = str(widget.toolTip() or "").strip()
        if not current:
            widget.setToolTip(text)
    except Exception:
        pass


def fit_text_control(widget: QWidget, *, point_size: int | None = None) -> QSize:
    """Ensure a text-bearing control is large enough for its current font.

    No fixed size is imposed.  Minimum geometry is derived from QFontMetrics,
    while obsolete finite maxima are released.  Controls can therefore grow on
    a high-DPI monitor and shrink again when moved back.
    """
    if bool(widget.property("smstSkipAdaptiveGeometry")):
        return widget.size()

    base_min_w, base_min_h, base_max_w, base_max_h = _remember_base_constraints(widget)
    font = QFont(widget.font())
    if point_size is not None and font.pointSizeF() <= 0:
        font.setPointSize(max(1, int(point_size)))
    metrics = QFontMetrics(font)
    try:
        hint = widget.sizeHint()
        hint_w = max(0, int(hint.width()))
        hint_h = max(0, int(hint.height()))
    except Exception:
        hint_w = hint_h = 0

    req_w = base_min_w
    req_h = base_min_h
    clear_w = False
    clear_h = False

    if isinstance(widget, QAbstractButton):
        text = _visible_button_text(widget)
        if not text:
            return QSize(max(widget.minimumWidth(), hint_w), max(widget.minimumHeight(), hint_h))
        icon_extra = 0
        try:
            icon = widget.icon()
            if icon is not None and not icon.isNull():
                icon_extra = max(16, int(widget.iconSize().width())) + 10
        except Exception:
            pass
        class_name = type(widget).__name__.lower()
        indicator_extra = 30 if ("check" in class_name or "radio" in class_name) else 0
        # Fluent buttons use generous internal left/right spacing; 36 logical
        # pixels prevents the last Chinese glyph from being clipped at 125–200%.
        req_w = max(
            base_min_w,
            hint_w,
            metrics.horizontalAdvance(text) + 36 + icon_extra + indicator_extra,
        )
        req_h = max(base_min_h, hint_h, metrics.height() + 18)
        clear_w = clear_h = True
        _set_full_text_tooltip(widget, text)
        policy = widget.sizePolicy()
        if policy.horizontalPolicy() == QSizePolicy.Policy.Fixed:
            widget.setSizePolicy(QSizePolicy.Policy.Minimum, policy.verticalPolicy())

    elif _is_combo_like(widget):
        texts: list[str] = []
        try:
            texts.append(str(widget.currentText() or ""))
            count = min(int(widget.count()), 500)
            texts.extend(str(widget.itemText(i) or "") for i in range(count))
        except Exception:
            pass
        longest = max(texts, key=lambda value: metrics.horizontalAdvance(value), default="")
        current = texts[0] if texts else ""
        # Cap product-name controls so one unusually long model cannot make a
        # dialog wider than its screen.  Full text remains available in popup
        # rows and tooltip.
        natural_w = metrics.horizontalAdvance(longest or current) + 58
        req_w = max(base_min_w, hint_w, min(560, natural_w))
        req_h = max(base_min_h, hint_h, metrics.height() + 18)
        clear_w = clear_h = True
        _set_full_text_tooltip(widget, current)

    elif _is_spin_like(widget):
        samples: list[str] = []
        for name in ("minimum", "maximum", "value"):
            getter = getattr(widget, name, None)
            if callable(getter):
                try:
                    samples.append(str(getter()))
                except Exception:
                    pass
        for name in ("prefix", "suffix"):
            getter = getattr(widget, name, None)
            if callable(getter):
                try:
                    samples.append(str(getter() or ""))
                except Exception:
                    pass
        sample = "".join(samples) or "0000"
        req_w = max(base_min_w, hint_w, min(360, metrics.horizontalAdvance(sample) + 76))
        req_h = max(base_min_h, hint_h, metrics.height() + 18)
        clear_w = clear_h = True

    elif isinstance(widget, QLineEdit):
        req_w = max(base_min_w, min(hint_w, 560))
        req_h = max(base_min_h, hint_h, metrics.height() + 18)
        clear_h = True

    elif isinstance(widget, QLabel):
        text = _plain_label_text(widget)
        req_h = max(base_min_h, hint_h, metrics.height() + 6)
        if text:
            _set_full_text_tooltip(widget, text)
        if widget.wordWrap():
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            available = max(120, int(widget.width()))
            if available <= 120 and widget.parentWidget() is not None:
                available = max(120, int(widget.parentWidget().contentsRect().width()) - 24)
            if text:
                rect = metrics.boundingRect(
                    QRect(0, 0, available, 10000),
                    int(Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs),
                    text,
                )
                req_h = max(req_h, rect.height() + 8)
            clear_h = True
        elif text and len(text) <= 80 and not bool(widget.property("smstAllowElide")):
            req_w = max(base_min_w, min(680, metrics.horizontalAdvance(text) + 10))
            clear_w = True

    elif isinstance(widget, QTabBar):
        try:
            widget.setUsesScrollButtons(True)
            widget.setElideMode(Qt.TextElideMode.ElideRight)
            widget.setExpanding(False)
        except Exception:
            pass
        req_h = max(base_min_h, hint_h, metrics.height() + 18)
        clear_h = True

    else:
        return QSize(max(widget.minimumWidth(), hint_w), max(widget.minimumHeight(), hint_h))

    widget.setMinimumSize(req_w, req_h)
    _clear_stale_fixed_limits(
        widget,
        required_width=req_w,
        required_height=req_h,
        clear_width=clear_w,
        clear_height=clear_h,
    )
    # Preserve intentional finite design maxima when they are still large
    # enough, but never allow them to fall below the measured minimum.
    if not clear_w and base_max_w < _QT_MAX_SIZE:
        widget.setMaximumWidth(max(base_max_w, req_w))
    if not clear_h and base_max_h < _QT_MAX_SIZE:
        widget.setMaximumHeight(max(base_max_h, req_h))
    return QSize(req_w, req_h)


def apply_table_font(table: QTableWidget, font: QFont, *, minimum_padding: int = 12) -> int:
    """Apply one font to table items/header and return a safe row height."""
    table.setFont(font)
    header_font = QFont(font)
    header_font.setWeight(QFont.Weight.DemiBold)
    table.horizontalHeader().setFont(header_font)

    for row in range(table.rowCount()):
        for column in range(table.columnCount()):
            item = table.item(row, column)
            if item is not None:
                item.setFont(font)
                try:
                    if item.text() and not item.toolTip():
                        item.setToolTip(item.text())
                except Exception:
                    pass

    row_height = max(34, QFontMetrics(font).height() + int(minimum_padding))
    table.verticalHeader().setDefaultSectionSize(row_height)
    table.verticalHeader().setMinimumSectionSize(row_height)
    table.horizontalHeader().setMinimumHeight(
        max(32, QFontMetrics(header_font).height() + 14)
    )
    try:
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    except Exception:
        pass
    return row_height


def adapt_table_geometry(table: QTableWidget, *, point_size: int | None = None) -> None:
    """Prevent clipping in headers, rows and embedded cell controls."""
    font = QFont(table.font())
    if point_size is not None and font.pointSizeF() <= 0:
        font.setPointSize(max(1, int(point_size)))
    metrics = QFontMetrics(font)
    header = table.horizontalHeader()
    header_font = QFont(header.font())
    header_metrics = QFontMetrics(header_font)
    header.setMinimumHeight(max(34, header_metrics.height() + 14))
    try:
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    except Exception:
        pass

    for column in range(table.columnCount()):
        item = table.horizontalHeaderItem(column)
        header_required = 40
        if item is not None:
            header_required = header_metrics.horizontalAdvance(item.text()) + 30
            try:
                if item.text() and not item.toolTip():
                    item.setToolTip(item.text())
            except Exception:
                pass
        cell_required = 0
        for row in range(table.rowCount()):
            cell = table.cellWidget(row, column)
            if cell is not None:
                try:
                    cell_required = max(
                        cell_required,
                        int(cell.minimumSizeHint().width()) + 8,
                        int(cell.sizeHint().width()) + 8,
                    )
                except Exception:
                    pass
            item = table.item(row, column)
            if item is not None:
                try:
                    if item.text() and not item.toolTip():
                        item.setToolTip(item.text())
                except Exception:
                    pass
        required = max(header_required, cell_required)
        mode = header.sectionResizeMode(column)
        if mode in (
            QHeaderView.ResizeMode.Fixed,
            QHeaderView.ResizeMode.Interactive,
            QHeaderView.ResizeMode.ResizeToContents,
        ):
            table.setColumnWidth(column, max(table.columnWidth(column), required))

    base_row = max(36, metrics.height() + 18)
    table.verticalHeader().setDefaultSectionSize(base_row)
    table.verticalHeader().setMinimumSectionSize(base_row)
    preserve_wrapped_height = bool(table.property("smstPreserveWrappedRowHeight"))
    for row in range(table.rowCount()):
        required = base_row
        if preserve_wrapped_height:
            # 实时属性表的换行行高由页面按最终列宽精确计算。全局 DPI
            # 几何刷新只允许继续增高，不能把已经展开的多行文字压回单行。
            required = max(required, int(table.rowHeight(row)))
        for column in range(table.columnCount()):
            cell = table.cellWidget(row, column)
            if cell is not None:
                try:
                    required = max(
                        required,
                        int(cell.minimumSizeHint().height()) + 8,
                        int(cell.sizeHint().height()) + 8,
                    )
                except Exception:
                    pass
        table.setRowHeight(row, required)


def _configure_form_layouts(root: QWidget) -> None:
    widgets = [root]
    try:
        widgets.extend(root.findChildren(QWidget))
    except Exception:
        pass
    for widget in widgets:
        try:
            layout = widget.layout()
        except Exception:
            layout = None
        if isinstance(layout, QFormLayout):
            try:
                layout.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
                layout.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
                layout.setFormAlignment(Qt.AlignmentFlag.AlignTop)
                layout.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            except Exception:
                pass


def apply_adaptive_geometry(
    root: QWidget,
    point_size: int | None = None,
    *,
    include_tables: bool = True,
) -> None:
    """Recalculate text-control geometry for an entire widget subtree."""
    widgets = [root]
    try:
        widgets.extend(root.findChildren(QWidget))
    except Exception:
        pass

    # Fit leaf controls before asking parent layouts to recalculate.
    for widget in reversed(widgets):
        if bool(widget.property("smstIndependentDataFont")):
            continue
        try:
            fit_text_control(widget, point_size=point_size)
        except Exception:
            pass

    _configure_form_layouts(root)

    if include_tables:
        try:
            tables = root.findChildren(QTableWidget)
        except Exception:
            tables = []
        if isinstance(root, QTableWidget):
            tables = [root, *tables]
        for table in tables:
            try:
                adapt_table_geometry(table, point_size=point_size)
            except Exception:
                pass

    # Scroll areas should resize their content instead of clipping it at the
    # viewport boundary.
    try:
        for area in root.findChildren(QScrollArea):
            area.setWidgetResizable(True)
    except Exception:
        pass

    try:
        layout = root.layout()
        if layout is not None:
            layout.invalidate()
            layout.activate()
        root.updateGeometry()
    except Exception:
        pass


def fit_window_to_screen(
    window: QWidget,
    *,
    preferred: tuple[int, int],
    minimum: tuple[int, int] = (640, 480),
    margin: tuple[int, int] = (24, 56),
) -> QSize:
    """Fit a top-level window inside the current screen's logical work area."""
    screen = _screen_for(window)
    hint = window.sizeHint()
    desired_w = max(int(preferred[0]), max(0, int(hint.width())))
    desired_h = max(int(preferred[1]), max(0, int(hint.height())))
    if screen is None:
        min_w, min_h = minimum
        pref_w, pref_h = desired_w, desired_h
    else:
        area = screen.availableGeometry()
        max_w = max(320, int(area.width()) - int(margin[0]))
        max_h = max(240, int(area.height()) - int(margin[1]))
        pref_w = min(desired_w, max_w)
        pref_h = min(desired_h, max_h)
        min_w = min(int(minimum[0]), max_w)
        min_h = min(int(minimum[1]), max_h)

    window.setMinimumSize(max(320, min_w), max(240, min_h))
    window.resize(max(window.minimumWidth(), pref_w), max(window.minimumHeight(), pref_h))
    return window.size()


def scoped_font_stylesheet(object_name: str, point_size: int) -> str:
    """Stylesheet for normal UI text under one root, excluding QTextEdit data."""
    root = f"QWidget#{object_name}"
    selectors = [
        f"{root} QLabel",
        f"{root} QPushButton",
        f"{root} QToolButton",
        f"{root} QCheckBox",
        f"{root} QRadioButton",
        f"{root} QLineEdit",
        f"{root} QComboBox",
        f"{root} QAbstractSpinBox",
        f"{root} QTableWidget",
        f"{root} QHeaderView::section",
        f"{root} QTabBar::tab",
    ]
    return (
        "\n"
        + ",\n".join(selectors)
        + f''' {{
            font-family: "{UI_FONT_FAMILY}";
            font-size: {int(point_size)}pt;
        }}\n'''
    )


def apply_scoped_font(root: QWidget, point_size: int) -> None:
    """Apply one font tree plus a scoped QSS override without accumulation."""
    point_size = max(1, int(point_size))
    signature = f"{UI_FONT_FAMILY}|{point_size}"
    if root.property("_smst_dpi_font_signature") != signature:
        root.setProperty("_smst_dpi_font_signature", signature)
        font = make_ui_font(point_size)
        root.setFont(font)
        try:
            for child in root.findChildren(QWidget):
                if child.property("smstIndependentDataFont"):
                    continue
                child.setFont(font)
        except Exception:
            pass

        object_name = str(root.objectName() or "").strip()
        if object_name:
            base_qss = root.property("_smst_dpi_font_base_qss")
            if base_qss is None:
                base_qss = root.styleSheet()
                root.setProperty("_smst_dpi_font_base_qss", base_qss)
            root.setStyleSheet(
                str(base_qss or "") + scoped_font_stylesheet(object_name, point_size)
            )

    apply_adaptive_geometry(root, point_size)


class _AdaptiveUiController(QObject):
    """Application-wide, debounced geometry repair for dynamic Fluent widgets."""

    _TRIGGER_EVENTS = {
        QEvent.Type.Show,
        QEvent.Type.Polish,
        QEvent.Type.FontChange,
        QEvent.Type.ApplicationFontChange,
        QEvent.Type.StyleChange,
        QEvent.Type.ScreenChangeInternal,
        QEvent.Type.LayoutRequest,
    }

    def __init__(self, app: QApplication):
        super().__init__(app)
        self._pending: dict[int, weakref.ReferenceType[QWidget]] = {}

    def _schedule(self, widget: QWidget) -> None:
        try:
            top = widget.window()
        except Exception:
            top = widget
        if top is None or bool(top.property("smstSkipGlobalAdaptiveUi")):
            return
        key = id(top)
        if key in self._pending:
            return
        self._pending[key] = weakref.ref(top)

        def run() -> None:
            ref = self._pending.pop(key, None)
            current = ref() if ref is not None else None
            if current is None:
                return
            try:
                apply_adaptive_geometry(current)
                current.updateGeometry()
            except Exception:
                pass

        QTimer.singleShot(_ADAPT_DEBOUNCE_MS, run)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if isinstance(watched, QWidget):
            event_type = event.type()
            if event_type in self._TRIGGER_EVENTS:
                self._schedule(watched)
            elif event_type == QEvent.Type.Resize and watched.isWindow():
                self._schedule(watched)
        return False


def install_adaptive_ui_controller(app: QApplication | None = None):
    """Install one application-level event filter for dynamically built UI."""
    global _CONTROLLER
    if _CONTROLLER is not None:
        return _CONTROLLER
    app = app or QApplication.instance()
    if app is None:
        return None
    controller = _AdaptiveUiController(app)
    app.installEventFilter(controller)
    _CONTROLLER = controller
    # Keep an application-owned reference for bindings that aggressively
    # garbage-collect Python QObject wrappers.
    setattr(app, "_smst_adaptive_ui_controller", controller)
    return controller
