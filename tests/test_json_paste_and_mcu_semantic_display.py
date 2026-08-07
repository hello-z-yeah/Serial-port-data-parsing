from pathlib import Path
from types import SimpleNamespace

from protocol_parser.attr_center import AttrStateCenter
from protocol_parser.ui_helpers import format_frame_display


def _center() -> AttrStateCenter:
    center = AttrStateCenter()
    center.load_product({
        "attributes": {
            "0x4C": {
                "name": "light",
                "cn_name": "照明",
                "typeid": 2,
                "access": "读写",
                "enum": {"0": "关闭", "1": "开启"},
                "snapshot_wire_id": 0x0C,
                "initial_value": 0,
            },
            "0x4D": {
                "name": "fan-level",
                "cn_name": "风挡",
                "typeid": 2,
                "access": "读写",
                "enum": {"1": "1档", "2": "高"},
                "snapshot_wire_id": 0x0D,
                "initial_value": 1,
            },
        }
    })
    return center


def test_json_editor_forces_plain_text_paste():
    source = Path("protocol_parser/product_import_dialog.py").read_text(encoding="utf-8")
    assert "class PlainJsonTextEdit" in source
    assert "self.textCursor().insertText(source.text())" in source
    assert "self.json_edit.setAcceptRichText(False)" in source


def test_command_display_recovers_product_semantics_from_wire_id():
    center = _center()
    result = SimpleNamespace(
        cmd_code="0x01",
        cmd_name="命令下发",
        direction="模组→MCU",
        fields=[{"name": "消息id", "type": "uint8", "value": 91, "text": "91"}],
    )
    raw = bytes.fromhex("A5 A5 03 01 00 04 5B 02 0C 01 BC")
    text = format_frame_display(result, raw, 1.0, attr_center=center)
    assert "消息id:91" in text
    assert "照明开启" in text


def test_status_report_display_uses_enum_business_label():
    center = _center()
    result = SimpleNamespace(
        cmd_code="0x10",
        cmd_name="状态上报",
        direction="MCU→模组",
        fields=[],
    )
    raw = bytes.fromhex("A5 A5 03 10 00 03 02 0D 02 71")
    text = format_frame_display(result, raw, 1.0, is_tx=True, attr_center=center)
    assert "状态上报" in text
    assert "风挡高" in text
