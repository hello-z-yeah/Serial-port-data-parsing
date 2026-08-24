from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MCU_PAGE = ROOT / "protocol_parser" / "mcu_page.py"
GUI = ROOT / "protocol_parser" / "gui.py"


def test_ctrl_wheel_zoom_filters_textedit_viewport():
    source = MCU_PAGE.read_text(encoding="utf-8")
    assert "self.viewport().installEventFilter(self)" in source
    assert "event.type() == QEvent.Type.Wheel" in source
    assert "self._handle_ctrl_wheel(event)" in source
    assert "self.set_data_font_point_size(" in source
    assert "immediate=False" in source
    assert "_font_zoom_timer" in source
    assert "_apply_pending_font_size" in source


def test_both_realtime_data_windows_use_data_font_textedit():
    mcu_source = MCU_PAGE.read_text(encoding="utf-8")
    gui_source = GUI.read_text(encoding="utf-8")
    assert "self.data_text = CtrlWheelZoomTextEdit" in mcu_source
    assert "self.serial_text = CtrlWheelZoomTextEdit" in gui_source


def test_both_realtime_data_windows_keep_hidden_font_size_state_controls():
    mcu_source = MCU_PAGE.read_text(encoding="utf-8")
    gui_source = GUI.read_text(encoding="utf-8")
    assert 'BodyLabel("字号："' in mcu_source
    assert "self.data_font_spin = SpinBox" in mcu_source
    assert "self.data_font_spin.valueChanged.connect(" in mcu_source
    assert 'BodyLabel("字号："' in gui_source
    assert "self.realtime_font_spin = SpinBox" in gui_source
    assert "self.realtime_font_spin.valueChanged.connect(" in gui_source
    assert "self.data_font_label.hide()" in mcu_source
    assert "self.data_font_spin.hide()" in mcu_source
    assert "self.realtime_font_label.hide()" in gui_source
    assert "self.realtime_font_spin.hide()" in gui_source


def test_data_font_size_is_scoped_to_realtime_text_documents():
    source = MCU_PAGE.read_text(encoding="utf-8")
    assert "def set_data_font_point_size" in source
    assert "setDefaultFont(document_font)" in source
    assert "self.setFont(widget_font)" in source
    assert "_FONT_MIN_PT = 8" in source
    assert "_FONT_MAX_PT = 24" in source


def test_normal_wheel_is_forwarded_to_native_scrolling():
    source = MCU_PAGE.read_text(encoding="utf-8")
    assert "if not (modifiers & Qt.KeyboardModifier.ControlModifier):" in source
    assert "super().wheelEvent(event)" in source

def test_font_size_spinboxes_reserve_visible_value_area():
    mcu_source = MCU_PAGE.read_text(encoding="utf-8")
    gui_source = GUI.read_text(encoding="utf-8")
    assert "self.data_font_spin.setMinimumWidth(132)" in mcu_source
    assert "self.data_font_spin.setMaximumWidth(148)" in mcu_source
    assert "self.data_font_spin.lineEdit().setMinimumWidth(48)" in mcu_source
    assert "self.realtime_font_spin.setMinimumWidth(132)" in gui_source
    assert "self.realtime_font_spin.setMaximumWidth(148)" in gui_source
    assert "self.realtime_font_spin.lineEdit().setMinimumWidth(48)" in gui_source


def test_realtime_textedit_defers_wrap_relayout_during_layout_freeze():
    source = MCU_PAGE.read_text(encoding="utf-8")
    assert "_WRAP_DEBOUNCE_BLOCK_THRESHOLD = 500" in source
    assert "def _should_defer_wrap_relayout" in source
    assert "setLineWrapMode(TextEdit.LineWrapMode.NoWrap)" in source
    assert "def _restore_line_wrap" in source


def test_main_window_pauses_display_flush_while_layout_is_frozen():
    source = GUI.read_text(encoding="utf-8")
    assert "def _set_data_display_frozen" in source
    assert "def is_data_display_frozen" in source
    assert "def _install_navigation_toggle_without_animation" in source
    assert "_DISPLAY_FLUSH_MAX_SEGMENTS_FROZEN" in source
    assert "frozen = self.is_data_display_frozen()" in source
    assert "panel.expand(useAni=False)" in source
    assert "_DISPLAY_FLUSH_MAX_SEGMENTS" in source
    assert "_DISPLAY_FLUSH_MAX_CHARS" in source


def test_mcu_and_monitor_use_capped_display_flushes():
    mcu_source = MCU_PAGE.read_text(encoding="utf-8")
    monitor_source = (ROOT / "protocol_parser" / "monitor_page.py").read_text(encoding="utf-8")
    assert "_FLUSH_MAX_SEGMENTS" in mcu_source
    assert "_FLUSH_MAX_CHARS" in mcu_source
    assert "def _colorization_enabled" in monitor_source

