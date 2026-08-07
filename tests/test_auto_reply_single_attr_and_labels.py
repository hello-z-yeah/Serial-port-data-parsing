from __future__ import annotations

from pathlib import Path

from protocol_parser.attr_center import AttrStateCenter
from protocol_parser.auto_cmd import AutoCmdEngine
from protocol_parser.auto_reply import AutoReplyEngine
from protocol_parser.parser import load_protocol, merge_protocol, parse_frame, split_frame
from protocol_parser.product_importer import build_product_cfg, parse_function_json


ROOT = Path(__file__).resolve().parents[1]


def _make_cfg() -> dict:
    attrs = parse_function_json([
        {
            "attrid": "0x01",
            "name": "power",
            "cn_name": "开关",
            "typeid": 0,
            "access": "读写",
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
            "name": "mode",
            "cn_name": "模式",
            "typeid": 2,
            "access": "读写",
        },
    ])
    user = build_product_cfg(
        product_name="单属性上报测试",
        pid="1",
        model="test.single.report",
        attributes=attrs,
        mcu_version="1.0.0",
    )
    cfg = merge_protocol(load_protocol(ROOT / "product" / "v3_serial.json"), user)
    cfg["product_info"] = user["product_info"]
    return cfg


def test_attrs_export_labels_use_attribute_key_and_name():
    attrs = parse_function_json({
        "Base": {},
        "Attrs": [
            {
                "serialId": 0,
                "attributeKey": "Volume",
                "attributeName": "Volume",
                "dataRwx": "rw",
                "type": 2,
                "dataType": "int",
                "dataValue": '{"min":0,"max":15,"step":1}',
                "nowValue": 0,
            },
            {
                "serialId": 1,
                "attributeKey": "play-control",
                "attributeName": "play-control",
                "dataRwx": "rw",
                "type": 2,
                "dataType": "enum",
                "dataValue": '{"0":"Play","1":"Pause"}',
                "nowValue": "0",
            },
        ],
    })
    assert attrs["0x00"]["cn_name"] == "音量"
    assert attrs["0x00"]["source_attribute_key"] == "Volume"
    assert attrs["0x00"]["source_attribute_name"] == "Volume"
    assert attrs["0x01"]["cn_name"] == "播放控制"


def test_auto_reply_reports_only_the_changed_attribute():
    cfg = _make_cfg()
    center = AttrStateCenter()
    center.load_product(cfg)
    cmd_engine = AutoCmdEngine(center)

    class Collector:
        def __init__(self) -> None:
            self.sent: list[bytes] = []

        def send(self, data: bytes) -> None:
            self.sent.append(bytes(data))

    collector = Collector()
    reply = AutoReplyEngine(collector, cmd_engine, center)
    reply.enable(True)

    request = cmd_engine.build_cmd_send(7, 0x01, True)
    result = parse_frame(request, cfg, direction="request")
    reply.on_frame(result, split_frame(request, cfg), 0.0)

    assert len(collector.sent) == 2
    ack_frame = split_frame(collector.sent[0], cfg)
    report_frame = split_frame(collector.sent[1], cfg)
    assert ack_frame.cmd_code == 0x01
    assert report_frame.cmd_code == 0x10
    # BOOL 单属性状态上报 = typeid + attrid + value，共 3 字节。
    assert report_frame.data == bytes([0x00, 0x01, 0x01])

    # 同一值再次下发也必须回复消息 ID，并对命令携带的这个属性再次
    # 状态上报，保证模组侧状态同步；但仍然只能上报这一项，不能上报全部。
    collector.sent.clear()
    reply.on_frame(result, split_frame(request, cfg), 1.0)
    assert len(collector.sent) == 2
    assert split_frame(collector.sent[0], cfg).cmd_code == 0x01
    repeated_report = split_frame(collector.sent[1], cfg)
    assert repeated_report.cmd_code == 0x10
    assert repeated_report.data == bytes([0x00, 0x01, 0x01])


def test_auto_reply_still_reports_when_another_channel_updated_state_first():
    cfg = _make_cfg()
    center = AttrStateCenter()
    center.load_product(cfg)
    cmd_engine = AutoCmdEngine(center)

    class Collector:
        def __init__(self) -> None:
            self.sent: list[bytes] = []

        def send(self, data: bytes) -> None:
            self.sent.append(bytes(data))

    collector = Collector()
    reply = AutoReplyEngine(collector, cmd_engine, center)
    reply.enable(True)

    request = cmd_engine.build_cmd_send(9, 0x03, 2)
    result = parse_frame(request, cfg, direction="request")

    # 模拟另一个接收/显示通道已提前更新属性中心。
    assert center.update_from_frame(result) == [0x03]
    reply.on_frame(result, split_frame(request, cfg), 0.0)

    assert len(collector.sent) == 2
    assert split_frame(collector.sent[0], cfg).cmd_code == 0x01
    report = split_frame(collector.sent[1], cfg)
    assert report.cmd_code == 0x10
    assert report.data == bytes([0x02, 0x03, 0x02])
