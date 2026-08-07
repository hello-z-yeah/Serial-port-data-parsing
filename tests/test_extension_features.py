from __future__ import annotations

import unittest
from pathlib import Path

from protocol_parser.attr_center import AttrStateCenter
from protocol_parser.auto_cmd import AutoCmdEngine
from protocol_parser.auto_reply import AutoReplyEngine
from protocol_parser.dev_info_encoder import encode_dev_info_frame
from protocol_parser.parser import load_protocol, merge_protocol, parse_frame, split_frame
from protocol_parser.serial_collector import SerialCollector
from protocol_parser.ui_helpers import format_frame_display
from protocol_parser.product_importer import (
    build_product_cfg,
    localized_attribute_name,
    parse_function_json,
)


ROOT = Path(__file__).resolve().parents[1]


def make_cfg():
    attrs = parse_function_json([
        {"attrid": "0x01", "name": "power", "cn_name": "开关", "typeid": 0, "access": "读写"},
        {"attrid": "0x02", "name": "temperature", "cn_name": "温度", "typeid": 2, "access": "只读"},
    ])
    user = build_product_cfg(
        product_name="测试产品", pid="123", model="test.model",
        attributes=attrs, mcu_version="1.2.3",
    )
    cfg = merge_protocol(load_protocol(ROOT / "product" / "v3_serial.json"), user)
    cfg["product_info"] = user["product_info"]
    return cfg


class ImporterTests(unittest.TestCase):
    def test_three_json_shapes(self):
        services = {"services": [{"iid": 2, "properties": [{"iid": 1, "name": "power", "format": "bool", "access": ["read", "write"]}]}]}
        self.assertTrue(parse_function_json(services))
        self.assertIn("0x01", parse_function_json({"0x01": {"name": "x", "typeid": 2}}))
        self.assertIn("0x02", parse_function_json([{"attrid": "0x02", "name": "y"}]))


    def test_hyphenated_value_list_and_range_are_imported(self):
        attrs = parse_function_json({
            "services": [{
                "iid": 2,
                "description": "Heater",
                "properties": [{
                    "iid": 2,
                    "name": "mode",
                    "format": "uint8",
                    "access": ["read", "write"],
                    "value-list": [
                        {"value": 0, "description": "Idle"},
                        {"value": 1, "description": "Heat"},
                        {"value": 2, "description": "Fan"},
                    ],
                    "value-range": [0, 2, 1],
                }],
            }],
        })
        self.assertEqual(attrs["0x42"]["enum"], {"0": "Idle", "1": "Heat", "2": "Fan"})
        self.assertEqual(attrs["0x42"]["range"], "[0,2]")
        self.assertEqual(attrs["0x42"]["step"], 1)

    def test_attribute_names_are_localized_for_ui(self):
        self.assertEqual(
            localized_attribute_name("Device Information-Device Model"),
            "设备信息-设备型号",
        )
        attrs = parse_function_json({
            "services": [{
                "iid": 2,
                "description": "Light",
                "properties": [{
                    "iid": 1,
                    "name": "on",
                    "format": "bool",
                    "access": ["read", "write"],
                }],
            }],
        })
        self.assertEqual(attrs["0x41"]["cn_name"], "照明-开关")


class CollectorIndependenceTests(unittest.TestCase):
    def test_mcu_parser_works_while_primary_collector_is_raw(self):
        cfg = make_cfg()
        center = AttrStateCenter()
        center.load_product(cfg)
        frame = AutoCmdEngine(center).build_heartbeat_req(1)
        received = []
        collector = SerialCollector(
            cfg=cfg,
            port="TEST",
            raw_mode=True,
            mcu_cfg=cfg,
            mcu_direction="request",
            on_mcu_frame=lambda result, parsed_frame, ts: received.append(
                (result.cmd_code if result else None, parsed_frame.raw)
            ),
        )
        collector.set_mcu_cfg(cfg)
        collector._dispatch_mcu_frames(frame, 1.0)
        self.assertEqual(received, [("0x20", frame)])


class McuDisplayTests(unittest.TestCase):
    def test_mcu_tx_heartbeat_uses_reply_name(self):
        cfg = make_cfg()
        center = AttrStateCenter()
        center.load_product(cfg)
        frame = AutoCmdEngine(center).build_heartbeat_resp(True)
        result = parse_frame(frame, cfg, direction="response")
        text = format_frame_display(result, frame, 1.0, is_tx=True)
        self.assertIn("回复心跳", text)
        self.assertIn("MCU→模组", text)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.cfg = make_cfg()
        self.center = AttrStateCenter()
        self.center.load_product(self.cfg)
        self.engine = AutoCmdEngine(self.center)

    def test_dev_info_contains_product_metadata(self):
        result = parse_frame(encode_dev_info_frame(self.cfg), self.cfg, direction="response")
        fields = {f.get("name"): f.get("value") for f in result.fields}
        self.assertEqual(fields.get("设备PID"), 123)
        self.assertEqual(fields.get("产品Model"), "test.model")

    def test_snapshot_and_report(self):
        snapshot = self.engine.build_snapshot_resp()
        self.assertEqual(split_frame(snapshot, self.cfg).cmd_code, 0x24)
        report = self.engine.build_attr_report()
        self.assertEqual(split_frame(report, self.cfg).cmd_code, 0x10)


    def test_miot_snapshot_matches_wire_serial_ids(self):
        base = load_protocol(ROOT / "product" / "v3_serial.json")
        user = load_protocol(ROOT / "tests" / "fixtures" / "legacy_miot_product.json")
        cfg = merge_protocol(base, user)
        for key in ("product_info", "source_function_json", "import_source"):
            if key in user:
                cfg[key] = user[key]

        center = AttrStateCenter()
        center.load_product(cfg)
        frame = AutoCmdEngine(center).build_snapshot_resp()
        expected = bytes.fromhex(
            "A5 A5 03 24 00 96 "
            "02 00 00 02 01 00 "
            "0B 02 00 0A 68 65 6C 6C 6F 77 6F 72 6C 64 "
            "02 04 00 02 05 00 02 06 00 "
            "0B 07 00 0A 68 65 6C 6C 6F 77 6F 72 6C 64 "
            "0B 08 00 0A 68 65 6C 6C 6F 77 6F 72 6C 64 "
            "0B 09 00 0A 68 65 6C 6C 6F 77 6F 72 6C 64 "
            "0B 0A 00 0A 68 65 6C 6C 6F 77 6F 72 6C 64 "
            "0B 0B 00 0A 68 65 6C 6C 6F 77 6F 72 6C 64 "
            "0B 0D 00 0A 68 65 6C 6C 6F 77 6F 72 6C 64 "
            "0B 0E 00 0A 68 65 6C 6C 6F 77 6F 72 6C 64 "
            "0B 0F 00 0A 68 65 6C 6C 6F 77 6F 72 6C 64 "
            "02 19 00 02 1A 00 02 1B 00 A7"
        )
        self.assertEqual(frame, expected)

    def test_attr_center_updates_from_cmd(self):
        frame = self.engine.build_cmd_send(7, 0x01, True)
        result = parse_frame(frame, self.cfg, direction="request")
        changed = self.center.update_from_frame(result)
        self.assertIn(0x01, changed)
        self.assertIs(self.center.get_attr_value(0x01)[1], True)

    def test_auto_reply_heartbeat(self):
        class FakeCollector:
            def __init__(self):
                self.sent = []

            def send(self, data):
                self.sent.append(bytes(data))

        collector = FakeCollector()
        reply = AutoReplyEngine(collector, self.engine, self.center)
        reply.enable(True)
        request = self.engine.build_heartbeat_req(1)
        result = parse_frame(request, self.cfg, direction="request")
        reply.on_frame(result, split_frame(request, self.cfg), 0.0)
        self.assertEqual(len(collector.sent), 1)
        self.assertEqual(split_frame(collector.sent[0], self.cfg).cmd_code, 0x20)


if __name__ == "__main__":
    unittest.main()
