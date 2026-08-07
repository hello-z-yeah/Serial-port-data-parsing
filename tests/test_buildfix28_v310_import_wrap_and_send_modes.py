from pathlib import Path

from protocol_parser.app_info import APP_VERSION
from protocol_parser.product_importer import parse_function_json

ROOT = Path(__file__).resolve().parents[1]
GUI = (ROOT / "protocol_parser" / "gui.py").read_text(encoding="utf-8")
MCU = (ROOT / "protocol_parser" / "mcu_page.py").read_text(encoding="utf-8")
DPI = (ROOT / "protocol_parser" / "dpi_font.py").read_text(encoding="utf-8")


def test_application_identity_is_v310_everywhere():
    assert APP_VERSION == "3.1.0"
    assert '#define MyAppVersion       "3.1.0"' in (ROOT / "installer" / "serial_port_parser.iss").read_text(encoding="utf-8-sig")
    version_info = (ROOT / "resources" / "version_info.txt").read_text(encoding="utf-8")
    assert "filevers=(3, 1, 0, 0)" in version_info
    assert "StringStruct('ProductVersion', '3.1.0')" in version_info


def test_receive_font_control_is_hidden_without_removing_logic():
    assert 'self.realtime_font_label = BodyLabel("字号：")' in GUI
    assert "self.realtime_font_label.hide()" in GUI
    assert "self.realtime_font_spin.hide()" in GUI
    assert "self.realtime_font_spin.valueChanged.connect(" in GUI
    toolbar_section = GUI.split("def _build_realtime_card", 1)[1].split("# 文本区", 1)[0]
    assert "basic_toolbar.addWidget(self.realtime_font_spin)" not in toolbar_section
    assert "basic_toolbar.addWidget(self.realtime_font_label)" not in toolbar_section


def test_wrapped_attribute_rows_preserve_full_height_across_dpi_updates():
    assert "class WrappedAttributeTextDelegate" in MCU
    assert "setItemDelegateForColumn(2" in MCU
    assert "setItemDelegateForColumn(3" in MCU
    assert 'setProperty("smstPreserveWrappedRowHeight", True)' in MCU
    assert 'table.property("smstPreserveWrappedRowHeight")' in DPI
    assert "QTimer.singleShot(160, self, self._resize_attr_rows_to_wrapped_content)" in MCU


def test_importer_preserves_or_translates_real_names_instead_of_attr_placeholders():
    attributes = parse_function_json({
        "Attrs": [
            {"serialId": 0, "attributeKey": "memory-location", "dataRwx": "w", "type": 2},
            {"serialId": 1, "display_name": "Motor Control", "dataRwx": "w", "type": 2},
            {"serialId": 2, "propertyName": "Unmapped Fancy Feature", "dataRwx": "r", "type": 2},
        ]
    })
    assert attributes["0x00"]["cn_name"] == "记忆位置"
    assert attributes["0x01"]["cn_name"] == "电机控制"
    assert attributes["0x02"]["cn_name"] == "Unmapped Fancy Feature"
    assert not attributes["0x02"]["cn_name"].startswith("属性0x")


def test_send_panel_and_command_library_carry_independent_display_formats():
    assert 'metadata={"display_format": "HEX", "send_source": "send_panel"}' in GUI
    assert 'metadata={"display_format": "ASCII", "send_source": "send_panel"}' in GUI
    assert 'metadata={"display_format": "HEX", "send_source": "command_library"}' in GUI
    assert 'metadata={"display_format": "ASCII", "send_source": "command_library"}' in GUI
    assert "item.get(\"type\") or (\"HEX\" if self._cmdlib_mode == \"hex\" else \"ASCII\")" in GUI or 'item.get("type") or ("HEX" if self._cmdlib_mode == "hex" else "ASCII")' in GUI
    assert "mode = self.send_mode" in GUI
    assert "self.collector.send_raw(" in GUI and "as_text=True" in GUI
    assert "tx_signal = Signal(bytes, float, object)" in GUI
    assert 'line = f"[{ts_str}] [TX] Raw-ASCII  | {shown}\\n"' in GUI or 'Raw-ASCII' in GUI
