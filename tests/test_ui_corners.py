"""Tests for unified Fluent widget corner-radius patching."""
from __future__ import annotations

from PySide6.QtWidgets import QApplication
from qfluentwidgets import CardWidget, ComboBox, PushButton, setTheme, Theme
from qfluentwidgets.common.style_sheet import FluentStyleSheet

from protocol_parser.ui_corners import (
    CORNER_RADIUS_PX,
    install_corner_radius_controller,
    patch_widget_corners,
)


def _qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_patch_widget_corners_updates_button_stylesheet():
    _qapp()
    setTheme(Theme.LIGHT)
    button = PushButton("demo")
    patch_widget_corners(button)
    assert f"border-radius: {CORNER_RADIUS_PX}px;" in button.styleSheet()


def test_patch_widget_corners_sets_card_border_radius():
    _qapp()
    card = CardWidget()
    patch_widget_corners(card)
    assert card.borderRadius == CORNER_RADIUS_PX


def test_install_corner_radius_controller_patches_on_polish():
    app = _qapp()
    install_corner_radius_controller(app)
    combo = ComboBox()
    combo.ensurePolished()
    assert bool(combo.property("_smst_corner_radius_patched"))
    assert f"border-radius: {CORNER_RADIUS_PX}px;" in combo.styleSheet()


def test_save_raw_restore_reapplies_button_patch():
    _qapp()
    setTheme(Theme.LIGHT)
    button = PushButton("开始存储数据")
    patch_widget_corners(button)
    button.setStyleSheet("QPushButton { background-color: #0078D4; border-radius: 12px; }")

    button.setProperty("_smst_corner_radius_patched", False)
    FluentStyleSheet.BUTTON.apply(button)
    patch_widget_corners(button)

    assert f"border-radius: {CORNER_RADIUS_PX}px;" in button.styleSheet()
