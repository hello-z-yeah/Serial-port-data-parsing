from __future__ import annotations

import json
from pathlib import Path

import pytest

from protocol_parser.attr_center import AttrStateCenter
from protocol_parser.auto_cmd import AutoCmdEngine
from protocol_parser.auto_reply import AutoReplyEngine
from protocol_parser.parser import encode_frame, load_protocol, merge_protocol, parse_frame, split_frame
from protocol_parser.product_importer import build_product_cfg, parse_function_json
from protocol_parser.ui_helpers import format_attr_validation_message

ROOT = Path(__file__).resolve().parents[1]


def make_cfg() -> dict:
    attrs = parse_function_json([
        {
            "attrid": "0x01",
            "name": "level",
            "cn_name": "档位",
            "typeid": 2,
            "access": "读写",
            "range": [0, 10],
            "step": 2,
        },
        {
            "attrid": "0x02",
            "name": "temperature",
            "cn_name": "温度",
            "typeid": 2,
            "access": "只读",
        },
        {
            "attrid": "0x03",
            "name": "command",
            "cn_name": "命令",
            "typeid": 2,
            "access": "只写",
        },
        {
            "attrid": "0x04",
            "name": "mode",
            "cn_name": "模式",
            "typeid": 2,
            "access": "读写",
            "enum": {"0": "关闭", "1": "开启"},
        },
        {
            "attrid": "0x05",
            "name": "label",
            "cn_name": "文本",
            "typeid": 11,
            "access": "读写",
            "range": {"length": 4},
        },
    ])
    user = build_product_cfg(
        product_name="完整逻辑测试",
        pid="1",
        model="test.complete.logic",
        attributes=attrs,
        mcu_version="1.0.0",
    )
    source = {
        "ActionEvent": [
            {
                "actionId": 7,
                "outputParams": [
                    {"attrid": 1, "value": 4},
                ],
            }
        ]
    }
    user["source_function_json"] = json.dumps(source, ensure_ascii=False)
    cfg = merge_protocol(load_protocol(ROOT / "product" / "v3_serial.json"), user)
    cfg["product_info"] = user["product_info"]
    cfg["source_function_json"] = user["source_function_json"]
    cfg["import_source"] = "json"
    return cfg


class Collector:
    def __init__(self) -> None:
        self.sent: list[bytes] = []

    def send(self, data: bytes) -> int:
        self.sent.append(bytes(data))
        return len(data)


def make_runtime():
    cfg = make_cfg()
    center = AttrStateCenter()
    center.load_product(cfg)
    engine = AutoCmdEngine(center)
    collector = Collector()
    marks: list[tuple[int, str]] = []
    reply = AutoReplyEngine(
        collector,
        engine,
        center,
        on_before_send=lambda count, name: marks.append((count, name)),
    )
    reply.enable(True)
    return cfg, center, engine, collector, reply, marks


def dispatch(reply, cfg, frame):
    result = parse_frame(frame, cfg, direction="request")
    reply.on_frame(result, split_frame(frame, cfg), 0.0)


def test_command_permissions_and_single_attribute_sync():
    cfg, center, engine, collector, reply, marks = make_runtime()

    dispatch(reply, cfg, engine.build_cmd_send(1, 0x01, 4))
    assert [split_frame(x, cfg).cmd_code for x in collector.sent] == [0x01, 0x10]
    assert center.get_attr_value(0x01)[1] == 4
    assert split_frame(collector.sent[1], cfg).data == bytes([0x02, 0x01, 0x04])
    assert marks == [(1, "自动回复收到模组命令"), (1, "自动回复收到模组命令")]

    collector.sent.clear()
    marks.clear()
    dispatch(reply, cfg, engine.build_cmd_send(2, 0x02, 8))
    assert [split_frame(x, cfg).cmd_code for x in collector.sent] == [0x01]
    assert center.get_attr_value(0x02)[1] == 0

    collector.sent.clear()
    dispatch(reply, cfg, engine.build_cmd_send(3, 0x03, 9))
    assert [split_frame(x, cfg).cmd_code for x in collector.sent] == [0x01]
    assert center.get_attr_value(0x03)[1] == 9


def test_get_attr_reply_excludes_write_only():
    cfg, center, engine, collector, reply, _ = make_runtime()
    center.set_attr_value(0x01, 4)
    center.set_attr_value(0x02, 7)
    frame = engine.build_get_attr_req(11, [0x01, 0x02, 0x03])
    dispatch(reply, cfg, frame)

    assert len(collector.sent) == 1
    response = parse_frame(collector.sent[0], cfg, direction="response")
    children = [
        child
        for field in response.fields
        if isinstance(field, dict)
        for child in (field.get("children") or [])
        if isinstance(child, dict)
    ]
    assert [child.get("attrid") for child in children] == ["0x01", "0x02"]


def test_value_validation_enum_range_step_and_string_length():
    _, center, _, _, _, _ = make_runtime()
    assert center.validate_attr_value(0x01, 4) == 4
    with pytest.raises(ValueError):
        center.validate_attr_value(0x01, 5)
    with pytest.raises(ValueError):
        center.validate_attr_value(0x01, 11)
    assert center.validate_attr_value(0x04, 1) == 1
    with pytest.raises(ValueError):
        center.validate_attr_value(0x04, 2)
    assert center.validate_attr_value(0x05, "abcd") == "abcd"
    with pytest.raises(ValueError):
        center.validate_attr_value(0x05, "abcde")


def test_action_response_uses_configured_outputs():
    cfg, center, _, collector, reply, _ = make_runtime()
    center.set_attr_value(0x01, 2)
    frame = encode_frame(
        0x12,
        cfg,
        direction="request",
        fields={"msg_id": 9, "action_id": 7, "actions": []},
    )
    dispatch(reply, cfg, frame)
    assert len(collector.sent) == 1
    response = parse_frame(collector.sent[0], cfg, direction="response")
    assert split_frame(collector.sent[0], cfg).cmd_code == 0x12
    children = [
        child
        for field in response.fields
        if isinstance(field, dict)
        for child in (field.get("children") or [])
        if isinstance(child, dict)
    ]
    assert any(child.get("attrid") == "0x01" and child.get("value_raw") == "4" for child in children)


def test_attribute_validation_prompt_is_a_user_hint_not_program_error():
    _, center, _, _, _, _ = make_runtime()
    entry = center.get_entry(0x01)
    assert entry is not None
    try:
        center.validate_attr_value(0x01, 11)
    except ValueError as exc:
        text = format_attr_validation_message(
            entry, 11, center.get_value_constraints(0x01), exc
        )
    else:
        raise AssertionError("expected validation failure")

    assert "输入值“11”不符合属性“档位”的取值要求" in text
    assert "允许范围：0–10" in text
    assert "取值步长：2" in text
    assert "请修改后重新发送" in text
    assert "程序遇到未知错误" not in text
    assert "error.log" not in text


def test_wise_write_only_enum_uses_legal_initial_value_for_generated_command():
    base = load_protocol(ROOT / "product" / "v3_serial.json")
    user = load_protocol(ROOT / "tests" / "fixtures" / "legacy_miot_product.json")
    cfg = merge_protocol(base, user)
    for key in ("product_info", "source_function_json", "import_source"):
        if key in user:
            cfg[key] = user[key]

    center = AttrStateCenter()
    center.load_product(cfg)
    entry = center.get_entry(0x10)
    assert entry is not None
    assert entry.cn_name == "一键切换"
    assert entry.access == "只写"
    assert center.get_valid_default_value(0x10) == 1

    frame = AutoCmdEngine(center).build_cmd_send(
        1, 0x10, center.get_valid_default_value(0x10)
    )
    parsed = split_frame(frame, cfg)
    assert parsed.cmd_code == 0x01
    assert parsed.data == bytes([0x01, 0x02, 0x10, 0x01])


def test_invalid_initial_value_falls_back_to_enum_or_range_default():
    attrs = parse_function_json([
        {
            "attrid": "0x01",
            "name": "mode",
            "cn_name": "模式",
            "typeid": 2,
            "access": "只写",
            "enum": {"1": "A", "2": "B"},
            "initial_value": 0,
        },
        {
            "attrid": "0x02",
            "name": "volume",
            "cn_name": "音量",
            "typeid": 2,
            "access": "读写",
            "range": [5, 15],
            "initial_value": 200,
        },
    ])
    user = build_product_cfg(
        product_name="默认值回退测试",
        pid="1",
        model="test.default.fallback",
        attributes=attrs,
    )
    cfg = merge_protocol(load_protocol(ROOT / "product" / "v3_serial.json"), user)
    cfg["product_info"] = user["product_info"]
    center = AttrStateCenter()
    center.load_product(cfg)

    assert center.get_attr_value(0x01)[1] == 1
    assert center.get_attr_value(0x02)[1] == 5


def test_unknown_or_invalid_command_is_not_acknowledged_or_partially_applied():
    cfg, center, engine, collector, reply, marks = make_runtime()

    # 同一帧包含一个合法属性和一个未知属性：整帧拒绝，不能部分修改后仍 ACK。
    frame = encode_frame(
        0x01,
        cfg,
        direction="request",
        fields={
            "msg_id": 21,
            "attrs": [(0x01, 4, 2), (0x7F, 1, 2)],
        },
    )
    dispatch(reply, cfg, frame)
    assert collector.sent == []
    assert marks == []
    assert center.get_attr_value(0x01)[1] == 0

    # 值 5 在协议 UINT8 上合法，但违反产品步长 2；同样不得 ACK 或污染状态。
    frame = encode_frame(
        0x01,
        cfg,
        direction="request",
        fields={"msg_id": 22, "attrs": [(0x01, 5, 2)]},
    )
    dispatch(reply, cfg, frame)
    assert collector.sent == []
    assert marks == []
    assert center.get_attr_value(0x01)[1] == 0

    # 底层组帧同样禁止把只写属性作为 MCU 状态上报，避免其他调用路径绕过 UI。
    with pytest.raises(ValueError):
        engine.build_attr_report([0x03])


def test_general_frame_update_rejects_product_invalid_value():
    cfg = make_cfg()
    center = AttrStateCenter()
    center.load_product(cfg)
    invalid_report = encode_frame(
        0x10,
        cfg,
        direction="request",
        fields=[(0x01, 5, 2)],
    )
    result = parse_frame(invalid_report, cfg, direction="request")
    assert center.update_from_frame(result) == []
    assert center.get_attr_value(0x01)[1] == 0


def _make_wise_runtime():
    base = load_protocol(ROOT / "product" / "v3_serial.json")
    user = load_protocol(ROOT / "tests" / "fixtures" / "legacy_miot_product.json")
    cfg = merge_protocol(base, user)
    for key in ("product_info", "source_function_json", "import_source"):
        if key in user:
            cfg[key] = user[key]
    center = AttrStateCenter()
    center.load_product(cfg)
    engine = AutoCmdEngine(center)
    collector = Collector()
    warnings: list[str] = []
    reply = AutoReplyEngine(collector, engine, center, on_error=warnings.append)
    reply.enable(True)
    return cfg, center, collector, reply, warnings


def test_exact_module_command_frames_are_acknowledged_and_synchronized():
    cfg, center, collector, reply, warnings = _make_wise_runtime()

    frames = [
        # Volume = 0: ACK + single attribute report.
        "A5 A5 03 01 00 04 01 00 00 00 53",
        # play-control = 0: ACK + single attribute report.
        "A5 A5 03 01 00 04 04 00 01 00 57",
        # switch is write-only: ACK only.
        "A5 A5 03 01 00 04 07 02 03 00 5E",
    ]
    expected_counts = [2, 2, 1]

    for hex_frame, expected_count in zip(frames, expected_counts):
        collector.sent.clear()
        frame = bytes.fromhex(hex_frame)
        result = parse_frame(frame, cfg, direction="request")
        sent_count = reply.on_frame(result, split_frame(frame, cfg), 0.0)
        assert sent_count == expected_count
        assert split_frame(collector.sent[0], cfg).cmd_code == 0x01
        if expected_count == 2:
            assert split_frame(collector.sent[1], cfg).cmd_code == 0x10

    assert center.get_attr_value(0x00)[1] == 0
    assert center.get_attr_value(0x01)[1] == 0
    assert center.get_attr_value(0x03)[1] == 0
    assert warnings == []


def test_command_reply_recovers_attributes_when_result_only_contains_message_id():
    from types import SimpleNamespace

    cfg, _, collector, reply, warnings = _make_wise_runtime()
    frame_bytes = bytes.fromhex("A5 A5 03 01 00 04 01 00 00 00 53")
    frame = split_frame(frame_bytes, cfg)
    # Simulate an old/custom command definition that parsed the message id but
    # omitted the following attr list from result.fields.
    incomplete_result = SimpleNamespace(
        cmd_code="0x01",
        direction="模组→MCU",
        fields=[{"name": "消息id", "value": 1}],
    )

    assert reply.on_frame(incomplete_result, frame, 0.0) == 2
    assert [split_frame(item, cfg).cmd_code for item in collector.sent] == [0x01, 0x10]
    assert reply.last_applied_attrids == [0x00]
    assert warnings == []


def test_global_auto_reply_reenable_restores_all_protocol_rules():
    _, _, _, reply, _ = _make_wise_runtime()
    reply.set_rule_enabled(0x01, False)
    assert reply.rules[0x01].enabled is False

    reply.enable(False)
    reply.enable(True, enable_all_rules=True)

    assert reply.enabled is True
    assert all(rule.enabled for rule in reply.rules.values())


def test_invalid_wise_command_value_is_rejected_without_false_success_ack():
    cfg, center, collector, reply, warnings = _make_wise_runtime()
    # play-mode only allows 0/1/2; value 30 must not be acknowledged.
    frame = bytes.fromhex("A5 A5 03 01 00 04 0F 02 04 1E 85")
    result = parse_frame(frame, cfg, direction="request")

    assert reply.on_frame(result, split_frame(frame, cfg), 0.0) == 0
    assert collector.sent == []
    assert center.get_attr_value(0x04)[1] == 0
    assert any("校验失败" in item for item in warnings)
