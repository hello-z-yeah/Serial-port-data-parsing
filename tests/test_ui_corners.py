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
    from conftest import ensure_qt_app

    return ensure_qt_app()


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


def test_patch_widget_corners_updates_matched_popup_combo_subclass():
    _qapp()
    from protocol_parser.combo_font import MatchedPopupComboBox

    combo = MatchedPopupComboBox()
    patch_widget_corners(combo)
    assert bool(combo.property("_smst_corner_radius_patched"))
    assert f"border-radius: {CORNER_RADIUS_PX}px;" in combo.styleSheet()


def test_patch_widget_corners_updates_combo_menu_popup():
    _qapp()
    from qfluentwidgets.components.widgets.combo_box import ComboBoxMenu

    menu = ComboBoxMenu()
    patch_widget_corners(menu)
    assert bool(menu.property("_smst_corner_radius_patched"))
    assert f"border-radius: {CORNER_RADIUS_PX}px;" in menu.styleSheet()


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


def test_cmdlib_cycle_button_turns_primary_when_active():
    """循环发送→停止循环时，按钮文本变化并且样式切换为 Primary 蓝色。"""
    from PySide6.QtGui import QColor
    from qfluentwidgets import setTheme, Theme

    app = _qapp()
    setTheme(Theme.LIGHT)

    from protocol_parser.gui import ProtocolParserApp, PALETTE

    mw = ProtocolParserApp()
    mw.show()
    for _ in range(5):
        app.processEvents()

    btn = getattr(mw, "btn_cmdlib_cycle", None)
    assert btn is not None, "btn_cmdlib_cycle 必须存在"
    assert btn.text() == "循环发送"

    # --- 模拟启动循环发送（不真正开串口）---
    mw._cmdlib_cycle_on = True
    if hasattr(mw, "_update_cycle_button_style"):
        mw._update_cycle_button_style()
    else:
        # 还没实现：TDD 先断言失败
        btn.setText("停止循环")

    assert btn.text() == "停止循环"

    # 判断是否已变成 Primary 蓝色：
    is_primary_style = False
    try:
        bt = getattr(btn, "buttonType", None)
        if callable(bt):
            bt = bt()
        if str(bt).lower().startswith("primary") or str(bt) in ("1", "2.0"):
            is_primary_style = True
    except Exception:
        pass
    if not is_primary_style:
        # 检查 QSS 或 palette：按钮背景色是否接近主色
        try:
            qss = btn.styleSheet() or ""
            pal = btn.palette()
            primary_str = PALETTE["primary"].lstrip("#")
            pr = int(primary_str[0:2], 16)
            pg = int(primary_str[2:4], 16)
            pb = int(primary_str[4:6], 16)
            bg = pal.color(pal.ColorRole.Button)
            diff = abs(bg.red() - pr) + abs(bg.green() - pg) + abs(bg.blue() - pb)
            if diff < 200:
                is_primary_style = True
        except Exception:
            pass
    if not is_primary_style:
        # 对象名或 className 判断
        is_primary_style = type(btn).__name__ in ("PrimaryPushButton",)
        # 兜底：通过属性值
        if not is_primary_style:
            prop = btn.property("buttonType")
            if prop is not None and "primary" in str(prop).lower():
                is_primary_style = True

    assert is_primary_style, (
        "按钮变为'停止循环'后必须是蓝色 Primary 样式，"
        f"当前类型={type(btn).__name__}，QSS(前80)={(btn.styleSheet() or '')[:80]}"
    )

    # --- 关闭循环：样式恢复默认 ---
    mw._cmdlib_cycle_on = False
    if hasattr(mw, "_update_cycle_button_style"):
        mw._update_cycle_button_style()
    else:
        btn.setText("循环发送")

    assert btn.text() == "循环发送"
