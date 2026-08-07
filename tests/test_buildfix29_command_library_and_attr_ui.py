from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI = (ROOT / "protocol_parser" / "gui.py").read_text(encoding="utf-8")
MCU = (ROOT / "protocol_parser" / "mcu_page.py").read_text(encoding="utf-8")
RECEIVE = (ROOT / "protocol_parser" / "receive_page.py").read_text(encoding="utf-8")


def test_command_library_crlf_toggle_shares_send_panel_state():
    assert 'self.btn_cmdlib_crlf = ToggleButton("加回车换行")' in GUI
    assert 'self.btn_cmdlib_crlf.toggled.connect(' in GUI
    assert 'self._safe(self._set_tx_append_crlf)' in GUI
    assert 'self.btn_crlf.toggled.connect(self._safe(self._set_tx_append_crlf))' in GUI
    sync = GUI.split("def _set_tx_append_crlf", 1)[1].split("def _on_send_once", 1)[0]
    assert 'for name in ("btn_crlf", "btn_cmdlib_crlf")' in sync
    assert "self.tx_append_crlf = value" in sync


def test_protocol_imports_do_not_write_command_library():
    save_section = MCU.split("def _save_product_from_dialog", 1)[1].split(
        "# ------------------------------------------------------------------\n    # Attribute actions", 1
    )[0]
    assert "_generate_product_commands" not in save_section
    generator = GUI.split("def _generate_product_commands", 1)[1].split(
        "def _fill_auto_cmdlib", 1
    )[0]
    assert "self._fill_auto_cmdlib" not in generator
    filler = GUI.split("def _fill_auto_cmdlib", 1)[1].split("def _show_protocol", 1)[0]
    assert "del commands" in filler
    assert "_cmdlib_hex =" not in filler
    assert 'if bool(raw.get("generated"))' in GUI


def test_wrapped_attr_delegate_preserves_fluent_background_and_dark_selection_text():
    assert "base_delegate=self._attr_base_delegate" in MCU
    delegate = MCU.split("class WrappedAttributeTextDelegate", 1)[1].split(
        "class CtrlWheelZoomTextEdit", 1
    )[0]
    assert "delegate.paint(painter, wrapped, index)" in delegate
    assert "QPalette.ColorRole.HighlightedText" in delegate
    table = MCU.split("def _build_attr_card", 1)[1].split(
        "def _on_select_all_toggled", 1
    )[0]
    assert "self.attr_table.setPalette(table_palette)" in table
    assert "QTableView#AttributeTable::item:selected" in table


def test_receive_basic_buttons_are_moved_to_page_header():
    assert "header_controls: QWidget | None = None" in RECEIVE
    assert "self.switch_layout.insertWidget(2, header_controls)" in RECEIVE
    assert "self.receive_basic_row" in GUI
    assert "self.receive_page.attach(" in GUI
    realtime_toolbar = GUI.split("def _build_realtime_card", 1)[1].split(
        "# 协议解析控件仍保留", 1
    )[0]
    assert "toolbar_box.addWidget(basic_row)" not in realtime_toolbar
    relayout = GUI.split("def _relayout_receive_toolbars", 1)[1].split(
        "def _build_realtime_card", 1
    )[0]
    assert "basic.setDirection(QBoxLayout.Direction.LeftToRight)" in relayout
