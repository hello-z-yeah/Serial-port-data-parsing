from __future__ import annotations

from pathlib import Path

from protocol_parser.attr_center import AttrStateCenter
from protocol_parser.auto_cmd import AutoCmdEngine
from protocol_parser.parser import load_protocol, merge_protocol, split_frame
from protocol_parser.product_importer import build_product_cfg, parse_function_json

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "action_event_export.json"
MCU = (ROOT / "protocol_parser" / "mcu_page.py").read_text(encoding="utf-8")


def _load_bundle() -> dict:
    raw = FIXTURE.read_text(encoding="utf-8")
    return parse_function_json(raw)


def _make_cfg(bundle: dict) -> dict:
    user = build_product_cfg(
        product_name="action-event-test",
        pid="1",
        model="test.model",
        attributes=bundle["attributes"],
        actions=bundle["actions"],
        events=bundle["events"],
    )
    cfg = merge_protocol(load_protocol(ROOT / "product" / "v3_serial.json"), user)
    center = AttrStateCenter()
    center.load_product(cfg)
    return cfg


def test_wise_action_event_import():
    bundle = _load_bundle()
    assert bundle["attributes"]["0x00"]["name"] == "power"
    assert len(bundle["actions"]) == 1
    assert len(bundle["events"]) == 1
    action = bundle["actions"][0]
    event = bundle["events"][0]
    assert action["serial_id"] == 0x10
    assert action["kind"] == "action"
    assert action["in_params"][0]["attrid"] == 1
    assert event["serial_id"] == 0x16
    assert event["out_params"][0]["attrid"] == 1


def test_wise_event_index_maps_to_reference_wire_ids():
    bundle = parse_function_json({
        "Attrs": [
            {"serialId": 0, "attributeKey": "flag", "dataRwx": "r", "type": 0},
            {"serialId": 3, "attributeKey": "level", "dataRwx": "rw", "type": 2},
        ],
        "ActionEvent": [
            {"serialId": 1, "type": "event", "name": "event-unparam", "comment": "无参事件"},
            {
                "serialId": 2,
                "type": "event",
                "name": "event-param",
                "comment": "带参事件",
                "outParams": [
                    {"serialId": 0, "type": 0, "value": 0},
                    {"serialId": 3, "type": 2, "value": 0},
                ],
            },
        ],
    })
    assert bundle["events"][0]["serial_id"] == 21
    assert bundle["events"][1]["serial_id"] == 22

    cfg = _make_cfg(bundle)
    engine = AutoCmdEngine(AttrStateCenter())
    engine._ac.load_product(cfg)

    no_param = engine.build_event_report(21, [])
    assert no_param.hex(" ") == "a5 a5 03 11 00 01 15 74"

    with_param = engine.build_event_report(22, [(0, 0, 0), (3, 0, 2)])
    assert with_param.hex(" ") == "a5 a5 03 11 00 07 16 00 00 00 02 03 00 80"


def test_miot_actions_events_import():
    bundle = parse_function_json({
        "services": [{
            "iid": 3,
            "type": "urn:xjiang-spec:service:data:00007801:test:1",
            "description": "data",
            "comment": "数据类型",
            "properties": [{
                "iid": 1,
                "format": "bool",
                "access": ["read", "write"],
                "comment": "bool",
            }],
            "actions": [{
                "iid": 1,
                "type": "urn:xjiang-spec:action:test:1",
                "description": "toggle",
                "comment": "切换",
                "in": [1],
            }],
            "events": [{
                "iid": 1,
                "type": "urn:xjiang-spec:event:test:1",
                "description": "changed",
                "comment": "变化",
                "arguments": [1],
            }],
        }],
    })
    assert len(bundle["actions"]) == 1
    assert len(bundle["events"]) == 1
    assert bundle["actions"][0]["serial_id"] == 21
    assert bundle["events"][0]["serial_id"] == 21
    assert bundle["events"][0]["out_params"][0]["attrid"] == 0x51


def test_build_product_cfg_persists_actions_events():
    bundle = _load_bundle()
    cfg = build_product_cfg(
        product_name="action-event-test",
        pid="1",
        model="test.model",
        attributes=bundle["attributes"],
        actions=bundle["actions"],
        events=bundle["events"],
    )
    assert len(cfg["actions"]) == 1
    assert len(cfg["events"]) == 1


def test_build_event_report_encodes_event_id_and_params():
    bundle = _load_bundle()
    cfg = _make_cfg(bundle)
    engine = AutoCmdEngine(AttrStateCenter())
    engine._ac.load_product(cfg)
    event = bundle["events"][0]
    event_id = int(event["serial_id"]) & 0xFF
    params = [
        (int(item["attrid"]) & 0xFF, 7, int(item["typeid"]))
        for item in event["out_params"]
    ]
    frame = engine.build_event_report(event_id, params)
    split = split_frame(frame, cfg)
    assert split.cmd_code == 0x11
    assert split.data[0] == event_id
    assert split.data[1:] == bytes.fromhex("02 01 07")


def test_mcu_page_has_action_event_panel():
    assert "action_event_visible_toggle" in MCU
    assert "动作/事件" in MCU
    assert "def _build_action_event_card" in MCU
    assert "def refresh_action_event_table" in MCU
    assert "def _on_event_report" in MCU
    assert "def _update_action_event_availability" in MCU
    assert "build_event_report" in MCU
    assert 'actions=bundle.get("actions")' in MCU
    assert "def _remeasure_action_event_columns" in MCU
