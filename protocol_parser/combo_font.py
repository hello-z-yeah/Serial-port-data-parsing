"""DPI-stable combo boxes whose popup text matches the closed control.

qfluentwidgets renders combo popups in a separate ``RoundMenu`` window.  On
some Windows DPI/resolution combinations that popup can inherit a smaller
application/menu font than the combo box itself.  The helpers in this module
copy the combo's *actual* font to every popup object after the menu is created,
so the closed control and dropdown list always use the same typeface and size.
"""
from __future__ import annotations

import weakref

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QAbstractItemView,
    QListView,
    QListWidget,
    QWidget,
)
from qfluentwidgets import ComboBox, EditableComboBox


def _is_widget_alive(widget: QWidget | None) -> bool:
    if widget is None:
        return False
    try:
        import shiboken6

        return bool(shiboken6.isValid(widget))
    except Exception:
        return True


def _font_qss(font: QFont) -> str:
    family = str(font.family() or "Microsoft YaHei UI").replace('"', "")
    if font.pointSizeF() > 0:
        size_rule = f"font-size: {font.pointSizeF():g}pt;"
    elif font.pixelSize() > 0:
        size_rule = f"font-size: {font.pixelSize()}px;"
    else:
        size_rule = ""
    raw_weight = font.weight()
    weight = int(getattr(raw_weight, "value", raw_weight))
    return (
        f'font-family: "{family}"; '
        f"{size_rule} "
        f"font-weight: {weight};"
    )


def sync_combo_popup_font(combo: QWidget) -> None:
    """Apply the combo box's exact current font to its popup menu and rows."""
    if not _is_widget_alive(combo):
        return

    font = QFont(combo.font())
    menu = getattr(combo, "dropMenu", None)
    if menu is None or not _is_widget_alive(menu):
        return

    targets: list[QWidget] = [menu]
    try:
        targets.extend(menu.findChildren(QWidget))
    except Exception:
        pass

    for target in targets:
        try:
            target.setFont(font)
        except Exception:
            pass

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

    views: list[QWidget] = []
    direct_view = getattr(menu, "view", None)
    if direct_view is not None:
        views.append(direct_view)
    for view_type in (QAbstractItemView, QListView, QListWidget):
        try:
            views.extend(menu.findChildren(view_type))
        except Exception:
            pass

    # Preserve order while removing duplicate QObject wrappers.
    unique_views: list[QWidget] = []
    seen_ids: set[int] = set()
    for view in views:
        if view is None or id(view) in seen_ids:
            continue
        seen_ids.add(id(view))
        unique_views.append(view)

    metrics = QFontMetrics(font)
    row_height = max(int(combo.height()), metrics.height() + 14)
    font_rule = _font_qss(font)
    signature = f"{font.toString()}|{row_height}"

    for view in unique_views:
        try:
            view.setFont(font)
            if view.property("_smst_combo_popup_font_signature") == signature:
                continue
            base_qss = view.property("_smst_combo_popup_base_qss")
            if base_qss is None:
                base_qss = view.styleSheet()
                view.setProperty("_smst_combo_popup_base_qss", base_qss)
            view.setProperty("_smst_combo_popup_font_signature", signature)
            view.setStyleSheet(
                str(base_qss or "")
                + f"""
                QAbstractItemView, QListView, QListWidget {{
                    {font_rule}
                }}
                QAbstractItemView::item, QListView::item, QListWidget::item {{
                    {font_rule}
                    min-height: {row_height}px;
                    padding-left: 10px;
                    padding-right: 10px;
                }}
                """
            )
        except Exception:
            pass

    # Some qfluentwidgets versions draw text in child labels/tool buttons rather
    # than the view delegate, so also give the menu itself a non-accumulating
    # font rule.
    try:
        if menu.property("_smst_combo_menu_font_signature") != signature:
            base_qss = menu.property("_smst_combo_menu_base_qss")
            if base_qss is None:
                base_qss = menu.styleSheet()
                menu.setProperty("_smst_combo_menu_base_qss", base_qss)
            menu.setProperty("_smst_combo_menu_font_signature", signature)
            menu.setStyleSheet(
                str(base_qss or "")
                + f"""
                QWidget, QLabel, QToolButton {{
                    {font_rule}
                }}
                """
            )
    except Exception:
        pass


def schedule_combo_popup_font_sync(combo: QWidget) -> None:
    """Synchronize immediately and after delayed popup child creation."""
    combo_ref = weakref.ref(combo)

    def apply() -> None:
        current = combo_ref()
        if _is_widget_alive(current):
            sync_combo_popup_font(current)

    # RoundMenu child widgets can be created one event-loop turn after
    # _showComboMenu(), especially on high-DPI Windows displays.
    for delay_ms in (0, 12, 40):
        QTimer.singleShot(delay_ms, apply)


class MatchedPopupComboBox(ComboBox):
    """ComboBox with popup rows locked to the control's current font."""

    def __init__(self, parent=None):
        super().__init__(parent)
        try:
            self.dropButton.clicked.connect(self._schedule_popup_font_sync)
        except Exception:
            pass

    def _schedule_popup_font_sync(self, checked: bool = False) -> None:
        del checked
        schedule_combo_popup_font_sync(self)

    def _showComboMenu(self) -> None:
        super()._showComboMenu()
        schedule_combo_popup_font_sync(self)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.FontChange,
            QEvent.Type.ApplicationFontChange,
            QEvent.Type.ScreenChangeInternal,
        ):
            schedule_combo_popup_font_sync(self)


class MatchedPopupEditableComboBox(EditableComboBox):
    """EditableComboBox with popup and line edit using one identical font."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sync_editor_font()
        try:
            self.dropButton.clicked.connect(self._schedule_popup_font_sync)
        except Exception:
            pass

    def _sync_editor_font(self) -> None:
        """Synchronize a wrapped editor when a library version provides one.

        Current qfluentwidgets ``EditableComboBox`` inherits ``LineEdit``
        directly, so the combo itself is the editor and has no ``lineEdit()``
        accessor.  Older/forked implementations may wrap an editor; only that
        distinct child needs an explicit font copy.
        """
        try:
            editor_attr = getattr(self, "lineEdit", None)
            editor = editor_attr() if callable(editor_attr) else editor_attr
            if editor is not None and editor is not self:
                editor.setFont(QFont(self.font()))
        except Exception:
            pass

    def setFont(self, font: QFont) -> None:
        super().setFont(font)
        self._sync_editor_font()

    def _schedule_popup_font_sync(self, checked: bool = False) -> None:
        del checked
        self._sync_editor_font()
        schedule_combo_popup_font_sync(self)

    def _showComboMenu(self) -> None:
        self._sync_editor_font()
        super()._showComboMenu()
        schedule_combo_popup_font_sync(self)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() in (
            QEvent.Type.FontChange,
            QEvent.Type.ApplicationFontChange,
            QEvent.Type.ScreenChangeInternal,
        ):
            self._sync_editor_font()
            schedule_combo_popup_font_sync(self)
