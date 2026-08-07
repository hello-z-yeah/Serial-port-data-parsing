from __future__ import annotations

import json
from types import SimpleNamespace

from protocol_parser.attr_center import AttrStateCenter
from protocol_parser.auto_cmd import AutoCmdEngine
from protocol_parser.auto_reply import AutoReplyEngine
from protocol_parser.parser import load_protocol, split_frame


class _Collector:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def send(self, data: bytes, metadata=None) -> int:  # noqa: ANN001
        del metadata
        payload = bytes(data)
        self.sent.append(payload)
        return len(payload)


def _miot_cfg() -> dict:
    cfg = load_protocol("product/v3_serial.json")
    services = [
        {
            "iid": 2,
            "description": "浴霸",
            "properties": [
                {"iid": 1, "description": "开关"},
                {"iid": 2, "description": "模式"},
                {"iid": 3, "description": "温度"},
                {"iid": 4, "description": "风速"},
            ],
        }
    ]
    cfg["source_function_json"] = json.dumps({"services": services}, ensure_ascii=False)
    cfg["import_source"] = "json"
    cfg["attributes"] = {
        "0x41": {
            "name": "power",
            "cn_name": "开关",
            "typeid": 2,
            "access": "读写",
            "enum": {"0": "关", "1": "开"},
            "initial_value": 0,
        },
        "0x42": {
            "name": "mode",
            "cn_name": "模式",
            "typeid": 2,
            "access": "读写",
            "range": "[0,10]",
            "initial_value": 0,
        },
        "0x43": {
            "name": "temperature",
            "cn_name": "温度",
            "typeid": 2,
            "access": "只读",
            "range": "[0,100]",
            "initial_value": 25,
        },
        "0x44": {
            "name": "fan-speed",
            "cn_name": "风速",
            "typeid": 2,
            "access": "读写",
            "range": "[0,10]",
            "initial_value": 0,
        },
    }
    return cfg


def _message_id_only_result(msg_id: int):
    # Matches the user's affected product: its custom 0x01 display definition
    # exposes only the message id, while the raw frame still contains attrs.
    return SimpleNamespace(
        cmd_code="0x01",
        direction="模组→MCU",
        fields=[
            {
                "name": "消息id",
                "type": "uint8",
                "value": msg_id,
                "text": str(msg_id),
            }
        ],
    )


def test_miot_sequential_wire_id_resolves_to_internal_attribute() -> None:
    center = AttrStateCenter()
    center.load_product(_miot_cfg())

    assert center.resolve_wire_attrid(0) == 0x41
    assert center.resolve_wire_attrid(1) == 0x42
    assert center.resolve_wire_attrid(2) == 0x43
    assert center.resolve_wire_attrid(3) == 0x44
    # Internal GUI id must not be mistaken for a line-protocol serialId when
    # the MIOT sequential mapping is active.
    assert center.resolve_wire_attrid(0x44) is None


def test_message_id_only_miot_command_replies_and_reports_exact_attribute() -> None:
    cfg = _miot_cfg()
    center = AttrStateCenter()
    center.load_product(cfg)
    collector = _Collector()
    reply = AutoReplyEngine(collector, AutoCmdEngine(center), center)
    reply.enable(True, enable_all_rules=True)

    # User capture: msg_id=2, UINT8, serialId=3, value=6.
    raw = bytes.fromhex("A5 A5 03 01 00 04 02 02 03 06 5F")
    frame = split_frame(raw, cfg)
    result = _message_id_only_result(2)

    sent_count = reply.on_frame(result, frame, 0.0)

    assert sent_count == 2
    assert any(
        child.get("attrid") == "0x03"
        for field in result.fields
        for child in (field.get("children") or [])
    )
    assert reply.last_applied_attrids == [0x44]
    assert center.get_attr_value(0x44)[1] == 6
    assert collector.sent == [
        bytes.fromhex("A5 A5 03 01 00 01 02 51"),
        bytes.fromhex("A5 A5 03 10 00 03 02 03 06 6B"),
    ]


def test_repeated_same_value_miot_command_still_synchronizes_attribute() -> None:
    cfg = _miot_cfg()
    center = AttrStateCenter()
    center.load_product(cfg)
    collector = _Collector()
    reply = AutoReplyEngine(collector, AutoCmdEngine(center), center)
    reply.enable(True, enable_all_rules=True)

    raw = bytes.fromhex("A5 A5 03 01 00 04 02 02 03 06 5F")
    frame = split_frame(raw, cfg)
    result = _message_id_only_result(2)

    assert reply.on_frame(result, frame, 0.0) == 2
    collector.sent.clear()
    assert reply.on_frame(result, frame, 1.0) == 2
    assert collector.sent[1] == bytes.fromhex("A5 A5 03 10 00 03 02 03 06 6B")
