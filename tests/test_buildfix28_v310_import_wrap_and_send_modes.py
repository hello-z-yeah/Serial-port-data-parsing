from pathlib import Path

from protocol_parser.app_info import APP_VERSION
from protocol_parser.product_importer import parse_function_json

ROOT = Path(__file__).resolve().parents[1]
GUI = (ROOT / "protocol_parser" / "gui.py").read_text(encoding="utf-8")
BRIDGE = (ROOT / "protocol_parser" / "gui_bridge.py").read_text(encoding="utf-8")
MCU = (ROOT / "protocol_parser" / "mcu_page.py").read_text(encoding="utf-8")
DPI = (ROOT / "protocol_parser" / "dpi_font.py").read_text(encoding="utf-8")


def test_application_identity_is_v330_everywhere():
    assert APP_VERSION == "3.3.3"
    assert '#define MyAppVersion       "3.3.3"' in (ROOT / "installer" / "serial_port_parser.iss").read_text(encoding="utf-8-sig")
    version_info = (ROOT / "resources" / "version_info.txt").read_text(encoding="utf-8")
    assert "filevers=(3, 3, 3, 0)" in version_info
    assert "StringStruct('ProductVersion', '3.3.3')" in version_info


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
    assert "QTimer.singleShot(160, self._resize_attr_rows_to_wrapped_content)" in MCU


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


def test_importer_maps_float_to_fixed_point_by_range_step_and_sign():
    # 有符号、一位小数 -> F1_I16
    attrs = parse_function_json({
        "services": [{
            "iid": 2,
            "description": "Environment",
            "properties": [{
                "iid": 1,
                "format": "float",
                "access": ["read", "notify"],
                "value-range": [-20, 120, 0.1],
                "description": "Temperature",
                "comment": "温度",
            }],
        }]
    })
    temp_attr = next(a for a in attrs.values() if a["cn_name"].endswith("温度"))
    assert temp_attr["typeid"] == 19

    # 无符号、一位小数 -> F1_U16
    attrs = parse_function_json({
        "services": [{
            "iid": 1,
            "properties": [{
                "iid": 1,
                "format": "float",
                "value-range": [0, 100, 0.1],
                "description": "Humidity",
            }],
        }]
    })
    assert list(attrs.values())[0]["typeid"] == 15

    # 有符号、两位小数 -> F2_I16
    attrs = parse_function_json({
        "services": [{
            "iid": 1,
            "properties": [{
                "iid": 1,
                "format": "float",
                "value-range": [-10, 10, 0.01],
                "description": "Calibration",
            }],
        }]
    })
    assert list(attrs.values())[0]["typeid"] == 20

    # 无符号、两位小数 -> F2_U16
    attrs = parse_function_json({
        "services": [{
            "iid": 1,
            "properties": [{
                "iid": 1,
                "format": "float",
                "value-range": [0, 100, 0.01],
                "description": "Precision",
            }],
        }]
    })
    assert list(attrs.values())[0]["typeid"] == 16


def test_send_panel_and_command_library_carry_independent_display_formats():
    assert 'metadata={"display_format": "HEX", "send_source": "send_panel"}' in GUI
    assert 'metadata={"display_format": "ASCII", "send_source": "send_panel"}' in GUI
    assert 'metadata={"display_format": "HEX", "send_source": "command_library"}' in GUI
    assert 'metadata={"display_format": "ASCII", "send_source": "command_library"}' in GUI
    assert "item.get(\"type\") or (\"HEX\" if self._cmdlib_mode == \"hex\" else \"ASCII\")" in GUI
    assert "mode = self.send_mode" in GUI
    assert "self.collector.send_raw(" in GUI and "as_text=True" in GUI
    assert "tx_signal = Signal(bytes, float, object)" in BRIDGE
    assert 'parts.append(f"[{ts_str}] [TX] Raw-ASCII {printable}\\n")' in GUI
    assert 'parts.append(f"{printable}\\n")' in GUI
