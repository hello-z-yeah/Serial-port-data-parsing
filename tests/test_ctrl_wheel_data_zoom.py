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
    assert "self.document().setDefaultFont(document_font)" in source
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

