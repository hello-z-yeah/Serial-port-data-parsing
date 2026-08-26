from __future__ import annotations

import json
from pathlib import Path

from protocol_parser.action_event_importer import _coerce_typeid
from protocol_parser.attr_center import AttrStateCenter
from protocol_parser.auto_cmd import AutoCmdEngine
from protocol_parser.auto_reply import AutoReplyEngine
from protocol_parser.parser import encode_frame, load_protocol, merge_protocol, parse_frame, split_frame
from protocol_parser.product_importer import build_product_cfg, parse_function_json
from protocol_parser.ui_helpers import _convert_value

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "wb28_miot_product.json"


def _wb28_cfg() -> dict:
    bundle = parse_function_json(FIXTURE.read_text(encoding="utf-8"))
    user = build_product_cfg(
        product_name="wb28",
        pid="1",
        model="wb28",
        attributes=bundle["attributes"],
        actions=bundle["actions"],
        events=bundle["events"],
    )
    return merge_protocol(load_protocol(ROOT / "product" / "v3_serial.json"), user)


def test_wb28_imports_only_xjiang_data_service_actions_events():
    bundle = parse_function_json(FIXTURE.read_text(encoding="utf-8"))
    assert len(bundle["actions"]) == 2
    assert len(bundle["events"]) == 2
    assert {a["serial_id"] for a in bundle["actions"]} == {21, 22}
    assert {e["serial_id"] for e in bundle["events"]} == {21, 22}
    assert all(item["service"] == "数据类型" for item in bundle["actions"] + bundle["events"])


def test_wb28_event_param_resolves_piid_arguments():
    bundle = parse_function_json(FIXTURE.read_text(encoding="utf-8"))
    event = next(e for e in bundle["events"] if e["service_iid"] == 2)
    assert len(event["out_params"]) == 2
    assert event["out_params"][0]["attrid"] == 0x54
    assert event["out_params"][0]["typeid"] == 2
    assert event["out_params"][1]["attrid"] == 0x51
    assert event["out_params"][1]["typeid"] == 0


def test_wb28_event_frames_match_reference_wire_ids():
    bundle = parse_function_json(FIXTURE.read_text(encoding="utf-8"))
    cfg = _wb28_cfg()
    center = AttrStateCenter()
    center.load_product(cfg)
    engine = AutoCmdEngine(center)

    unparam = next(e for e in bundle["events"] if e["service_iid"] == 1)
    frame = engine.build_event_report(unparam["serial_id"], [])
    assert frame.hex(" ") == "a5 a5 03 11 00 01 15 74"

    param = next(e for e in bundle["events"] if e["service_iid"] == 2)
    payload = [
        (int(p["attrid"]), 0, int(p["typeid"]))
        for p in param["out_params"]
    ]
    frame = engine.build_event_report(param["serial_id"], payload)
    split = split_frame(frame, cfg)
    assert split.cmd_code == 0x11
    assert split.data[0] == 22
    assert split.data[1:] == bytes.fromhex("02 54 00 00 51 00")


def test_wb28_merge_protocol_preserves_actions_events():
    cfg = _wb28_cfg()
    assert len(cfg.get("actions") or []) == 2
    assert len(cfg.get("events") or []) == 2
    assert cfg["events"][1]["cn_name"] == "事件上报带参"


def test_wb28_event_parse_shows_imported_label():
    cfg = _wb28_cfg()
    frame = bytes.fromhex("a5 a5 03 11 00 01 15 74")
    result = parse_frame(frame, cfg, direction="response")
    event_field = next(f for f in result.fields if f.get("name") == "事件 Event ID")
    assert "事件上报不带参" in str(event_field.get("text") or "")


def test_wb28_event_bool_param_accepts_false_and_zero():
    assert _convert_value("False", 0) is False
    assert _convert_value("0", 0) is False
    assert _convert_value("1", 0) is True
    # typeid=0 不能被 `or 2` 误判为 uint8
    assert _coerce_typeid(0, 2) == 0


def test_wb28_dev_info_includes_action_event_mapping():
    from protocol_parser.dev_info_encoder import build_dev_info_data

    bundle = parse_function_json(FIXTURE.read_text(encoding="utf-8"))
    data_attrs = {
        key: meta
        for key, meta in bundle["attributes"].items()
        if int(meta.get("source_siid", 0) or 0) == 3
    }
    user = build_product_cfg(
        product_name="wb28",
        pid="11747",
        model="xjiang.curtain.wb28",
        attributes=data_attrs,
        actions=bundle["actions"],
        events=bundle["events"],
    )
    user["source_function_json"] = FIXTURE.read_text(encoding="utf-8")
    user["product_info"]["mcu_version"] = [0, 0, 9]
    cfg = merge_protocol(load_protocol(ROOT / "product" / "v3_serial.json"), user)
    data = build_dev_info_data(cfg)
    tail = bytes.fromhex(
        "03 01 00 01 0E 14 "
        "03 01 00 02 0E 15 "
        "03 02 00 01 0E 16 "
        "03 02 00 02 0E 17"
    )
    assert data.endswith(tail), data.hex(" ")
    cfg = _wb28_cfg()
    center = AttrStateCenter()
    center.load_product(cfg)
    center.set_attr_value(0x51, 1)

    class Collector:
        def __init__(self) -> None:
            self.sent: list[bytes] = []

        def send(self, data: bytes) -> int:
            self.sent.append(bytes(data))
            return len(data)

    collector = Collector()
    engine = AutoCmdEngine(center)
    reply = AutoReplyEngine(collector, engine, center)
    reply.enable(True)

    action = next(a for a in cfg["actions"] if a["service_iid"] == 2)
    frame = encode_frame(
        0x12,
        cfg,
        direction="request",
        fields={"msg_id": 1, "action_id": action["serial_id"], "actions": []},
    )
    result = parse_frame(frame, cfg, direction="request")
    reply.on_frame(result, split_frame(frame, cfg), 0.0)
    assert len(collector.sent) == 1
    response = parse_frame(collector.sent[0], cfg, direction="response")
    children = [
        child
        for field in response.fields
        if isinstance(field, dict)
        for child in (field.get("children") or [])
        if isinstance(child, dict)
    ]
    assert any(child.get("attrid") == "0x51" for child in children)
