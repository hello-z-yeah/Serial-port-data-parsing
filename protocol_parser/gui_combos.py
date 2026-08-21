"""Fluent combo boxes shared by the main window and modal dialogs."""
from __future__ import annotations

import time

from protocol_parser.combo_font import (
    MatchedPopupComboBox,
    MatchedPopupEditableComboBox,
)
from protocol_parser.dpi_font import responsive_point_size
from protocol_parser.log_text_style import make_crisp_ui_font


class DpiAwareComboBox(MatchedPopupComboBox):
    """Fluent combo whose popup inherits the exact same font and row scale."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(make_crisp_ui_font(responsive_point_size(self, maximum=13)))


class ToggleCloseEditableComboBox(MatchedPopupEditableComboBox):
    """可编辑下拉框：再次点击箭头时可靠收回菜单。"""

    _REOPEN_GUARD_SECONDS = 0.18

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(make_crisp_ui_font(responsive_point_size(self, maximum=13)))
        self._drop_menu_closed_at = 0.0

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

        if self.dropMenu is not None:
            self._closeComboMenu()
            return

        if time.monotonic() - self._drop_menu_closed_at < self._REOPEN_GUARD_SECONDS:
            return

        self._showComboMenu()

    def _showComboMenu(self) -> None:
        super()._showComboMenu()
