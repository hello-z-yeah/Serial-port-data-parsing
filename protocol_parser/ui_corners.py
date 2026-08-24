"""Unified corner radius for qfluentwidgets controls.

qfluentwidgets registers per-widget stylesheets (PushButton, LineEdit, …).
Rules on the main window or QApplication do not override those sheets reliably.
CardWidget draws rounded corners via ``setBorderRadius``, not QSS alone.
"""
from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import QApplication, QWidget

from qfluentwidgets import (
    CardWidget,
    ComboBox,
    EditableComboBox,
    HeaderCardWidget,
    HyperlinkButton,
    LineEdit,
    ModelComboBox,
    PlainTextEdit,
    PrimaryDropDownPushButton,
    PrimaryPushButton,
    PushButton,
    SimpleCardWidget,
    TextEdit,
    ToggleButton,
    ToggleToolButton,
    ToolButton,
    TransparentPushButton,
)
from qfluentwidgets.common.style_sheet import getStyleSheet, setCustomStyleSheet, styleSheetManager
from qfluentwidgets.components.widgets.menu import RoundMenu

try:
    from qfluentwidgets import DropDownPushButton
except ImportError:  # pragma: no cover - older qfluentwidgets
    DropDownPushButton = PushButton  # type: ignore[misc,assignment]

try:
    from qfluentwidgets.common.config import qconfig
except ImportError:  # pragma: no cover - defensive for older qfluentwidgets
    qconfig = None  # type: ignore[assignment]

CORNER_RADIUS_PX = 12

_BUTTON_PATCH = f"""
PushButton, ToolButton, ToggleButton, ToggleToolButton,
PrimaryPushButton, PrimaryToolButton {{
    border-radius: {CORNER_RADIUS_PX}px;
}}
"""

_LINE_EDIT_PATCH = f"""
LineEdit, TextEdit, PlainTextEdit, TextBrowser {{
    border-radius: {CORNER_RADIUS_PX}px;
}}
"""

_COMBO_PATCH = f"""
ComboBox, EditableComboBox, ModelComboBox {{
    border-radius: {CORNER_RADIUS_PX}px;
}}
"""

_MENU_PATCH = f"""
MenuActionListWidget {{
    border-radius: {CORNER_RADIUS_PX}px;
}}
"""

_BUTTON_TYPES = (
    PushButton,
    PrimaryPushButton,
    ToggleButton,
    ToolButton,
    ToggleToolButton,
    TransparentPushButton,
    HyperlinkButton,
    DropDownPushButton,
    PrimaryDropDownPushButton,
)
_COMBO_TYPES = (ComboBox, EditableComboBox, ModelComboBox)
_LINE_EDIT_TYPES = (LineEdit, TextEdit, PlainTextEdit)
_CARD_TYPES = (CardWidget, SimpleCardWidget, HeaderCardWidget)

_PATCH_FLAG = "_smst_corner_radius_patched"


def _refresh_fluent_stylesheet(widget: QWidget) -> None:
    """Re-apply the composed Fluent stylesheet after a custom patch."""
    source = styleSheetManager.source(widget)
    if not source.sources:
        return
    theme = qconfig.theme if qconfig is not None else None
    widget.setStyleSheet(getStyleSheet(source, theme))


def patch_widget_corners(widget: QWidget) -> bool:
    """Apply the shared corner radius to one Fluent widget."""
    if bool(widget.property(_PATCH_FLAG)):
        return False

    if isinstance(widget, RoundMenu):
        setCustomStyleSheet(widget, _MENU_PATCH, _MENU_PATCH)
        _refresh_fluent_stylesheet(widget)
        widget.setProperty(_PATCH_FLAG, True)
        return True
    if isinstance(widget, _COMBO_TYPES):
        setCustomStyleSheet(widget, _COMBO_PATCH, _COMBO_PATCH)
        _refresh_fluent_stylesheet(widget)
        widget.setProperty(_PATCH_FLAG, True)
        return True
    if isinstance(widget, _BUTTON_TYPES):
        setCustomStyleSheet(widget, _BUTTON_PATCH, _BUTTON_PATCH)
        _refresh_fluent_stylesheet(widget)
        widget.setProperty(_PATCH_FLAG, True)
        return True
    if isinstance(widget, _LINE_EDIT_TYPES):
        setCustomStyleSheet(widget, _LINE_EDIT_PATCH, _LINE_EDIT_PATCH)
        _refresh_fluent_stylesheet(widget)
        widget.setProperty(_PATCH_FLAG, True)
        return True
    if isinstance(widget, _CARD_TYPES):
        setter = getattr(widget, "setBorderRadius", None)
        if callable(setter):
            setter(CORNER_RADIUS_PX)
            widget.setProperty(_PATCH_FLAG, True)
            return True
    return False


class CornerRadiusController(QObject):
    """Patch Fluent widgets when Qt polishes them."""

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if event.type() == QEvent.Type.Polish and isinstance(watched, QWidget):
            patch_widget_corners(watched)
        return False


def install_corner_radius_controller(
    app: QApplication | None = None,
) -> CornerRadiusController:
    app = app or QApplication.instance()
    if app is None:
        raise RuntimeError("QApplication is required before installing corner radius patches")

    existing = getattr(app, "_smst_corner_radius_controller", None)
    if isinstance(existing, CornerRadiusController):
        return existing

    controller = CornerRadiusController(app)
    app.installEventFilter(controller)
    setattr(app, "_smst_corner_radius_controller", controller)
    return controller
