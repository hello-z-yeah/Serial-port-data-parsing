from pathlib import Path
from types import SimpleNamespace

from protocol_parser.attr_center import AttrStateCenter
from protocol_parser.ui_helpers import format_frame_display

ROOT = Path(__file__).resolve().parents[1]
MCU_SOURCE = (ROOT / "protocol_parser" / "mcu_page.py").read_text(encoding="utf-8")


def _center() -> AttrStateCenter:
    center = AttrStateCenter()
    center.load_product(
        {
            "attributes": {
                "0x41": {
                    "name": "light-mode",
                    "cn_name": "照明-模式",
                    "typeid": 2,
                    "access": "读写",
                    "enum": {"5": "YHQ"},
                    "snapshot_wire_id": 0x04,
                    "initial_value": 0,
                },
                "0x45": {
                    "name": "other",
                    "cn_name": "其他",
                    "typeid": 2,
                    "access": "读写",
                    "snapshot_wire_id": 0x05,
                    "initial_value": 0,
                },
            }
        }
    )
    return center


def test_realtime_attr_id_column_displays_wire_id_without_changing_internal_row_key():
    refresh = MCU_SOURCE.split("def refresh_attr_table", 1)[1].split(
        "# ------------------------------------------------------------------\n    # Product import / switching", 1
    )[0]
    assert "build_snapshot_attrid_map" in refresh
    assert "wire_id = entry.attrid" in refresh
    assert "canonical_map.get(entry.attrid, entry.attrid)" in refresh
    assert 'self._readonly_item(f"0x{wire_id:02X}")' in refresh
    # Internal ID remains the logic/index key.
    assert "self._attr_row_by_id[entry.attrid] = row" in refresh


def test_status_report_log_includes_typeid_wire_attrid_and_data():
    center = _center()
    result = SimpleNamespace(
        cmd_code="0x10",
        cmd_name="状态上报",
        direction="MCU→模组",
        fields=[],
    )
    # data = 02 04 05 -> typeid=02, wire attrid=04, data=05
    raw = bytes.fromhex("A5 A5 03 10 00 03 02 04 05 00")
    text = format_frame_display(result, raw, 1.0, is_tx=True, attr_center=center)
    assert "照明-模式YHQ" in text
    assert "Typeid:02 Attrid:04 Data:05" in text


def test_command_dispatch_log_includes_typeid_wire_attrid_and_data():
    center = _center()
    result = SimpleNamespace(
        cmd_code="0x01",
        cmd_name="命令下发",
        direction="模组→MCU",
        fields=[{"name": "消息id", "type": "uint8", "value": 7, "text": "7"}],
    )
    # data = messageId 07 + 02 04 05
    raw = bytes.fromhex("A5 A5 03 01 00 04 07 02 04 05 00")
    text = format_frame_display(result, raw, 1.0, attr_center=center)
    assert "消息id:7" in text
    assert "照明-模式YHQ" in text
    assert "Typeid:02 Attrid:04 Data:05" in text
