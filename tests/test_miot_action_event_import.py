from __future__ import annotations

from pathlib import Path

from protocol_parser.action_event_importer import lookup_action_event_label
from protocol_parser.auto_cmd import AutoCmdEngine
from protocol_parser.attr_center import AttrStateCenter
from protocol_parser.dev_info_encoder import build_dev_info_data
from protocol_parser.parser import load_protocol, merge_protocol, split_frame
from protocol_parser.product_importer import build_product_cfg, parse_function_json

ROOT = Path(__file__).resolve().parents[1]
BARDIS_FIXTURE = ROOT / "tests" / "fixtures" / "bardis_yb1590.json"


def test_bardis_imports_standard_miot_stop_working_action():
    bundle = parse_function_json(BARDIS_FIXTURE.read_text(encoding="utf-8"))
    assert len(bundle["actions"]) == 1
    assert len(bundle["events"]) == 0
    action = bundle["actions"][0]
    assert action["serial_id"] == 21
    assert action["service_siid"] == 3
    assert action["service_iid"] == 1
    assert action["cn_name"] == "待机"
    assert action["service"] == "浴霸风暖"


def test_generic_miot_product_imports_actions_from_multiple_services():
    bundle = parse_function_json({
        "services": [
            {
                "iid": 2,
                "type": "urn:miot-spec-v2:service:light:00007802:demo:1",
                "description": "Light",
                "properties": [
                    {"iid": 1, "format": "bool", "access": ["read", "write"], "comment": "power"},
                ],
                "actions": [
                    {
                        "iid": 1,
                        "type": "urn:miot-spec-v2:action:toggle:00002801:demo:1",
                        "description": "Toggle",
                        "comment": "切换",
                    }
                ],
            },
            {
                "iid": 3,
                "type": "urn:miot-spec-v2:service:fan:00007809:demo:1",
                "description": "Fan",
                "properties": [
                    {"iid": 1, "format": "bool", "access": ["read"], "comment": "status"},
                ],
                "events": [
                    {
                        "iid": 1,
                        "type": "urn:miot-spec-v2:event:overheat:00005001:demo:1",
                        "description": "Overheat",
                        "comment": "过热",
                        "arguments": [],
                    }
                ],
            },
        ],
    })
    assert len(bundle["actions"]) == 1
    assert len(bundle["events"]) == 1
    assert bundle["actions"][0]["cn_name"] == "切换"
    assert bundle["actions"][0]["serial_id"] == 21
    assert bundle["events"][0]["cn_name"] == "过热"
    assert bundle["events"][0]["serial_id"] == 21


def test_bardis_action_resp_and_dev_info_mapping():
    bundle = parse_function_json(BARDIS_FIXTURE.read_text(encoding="utf-8"))
    user = build_product_cfg(
        product_name="bardis-yb1590",
        pid="37343",
        model="bardis.bhf_light.yb1590",
        attributes=bundle["attributes"],
        actions=bundle["actions"],
        events=bundle["events"],
    )
    user["source_function_json"] = BARDIS_FIXTURE.read_text(encoding="utf-8")
    cfg = merge_protocol(load_protocol(ROOT / "product" / "v3_serial.json"), user)

    data = build_dev_info_data(cfg)
    assert bytes.fromhex("03 01 00 01 0e 14") in data

    center = AttrStateCenter()
    center.load_product(cfg)
    engine = AutoCmdEngine(center)
    frame = engine.build_action_resp(msg_id=1, action_id=21, out_params=[])
    split = split_frame(frame, cfg)
    assert split.cmd_code == 0x12
    assert split.data[0] == 1
    assert split.data[1] == 21

    label = lookup_action_event_label(cfg, "action", 21)
    assert label == "待机"
