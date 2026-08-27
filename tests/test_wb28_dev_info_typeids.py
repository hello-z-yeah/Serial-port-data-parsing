from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from protocol_parser.dev_info_encoder import build_dev_info_data, encode_dev_info_frame
from protocol_parser.parser import load_protocol, merge_protocol, split_frame
from protocol_parser.product_importer import build_product_cfg, parse_function_json
from protocol_parser.ui_helpers import _format_attr_semantics

ROOT = Path(__file__).resolve().parents[1]
WB28_FIXTURE = ROOT / "tests" / "fixtures" / "wb28_miot_product.json"

REFERENCE_DEV_INFO_DATA = bytes.fromhex(
    "01 00 00 06 F7 00 00 2D E3 0B F5 00 13 "
    "78 6A 69 61 6E 67 2E 63 75 72 74 61 69 6E 2E 77 62 32 38 "
    "0E F3 00 8A "
    "00 00 03 00 00 01 02 01 03 00 00 02 01 02 03 00 00 03 02 03 03 00 00 04 03 04 03 00 00 05 04 05 03 00 00 06 05 06 03 00 00 07 06 07 03 00 00 08 0B 08 03 00 00 09 0E 09 03 00 00 0A 0F 0A 03 00 00 0B 10 0B 03 00 00 0C 11 0C 03 00 00 0D 12 0D 03 00 00 0E 13 0E 03 00 00 0F 14 0F 03 00 00 10 15 10 03 00 00 11 16 11 03 00 00 12 0E 12 03 00 00 13 0E 13 03 01 00 01 0E 14 03 01 00 02 0E 15 03 02 00 01 0E 16 03 02 00 02"
)


def _full_data_service_props():
    base = [
        ("bool", 1, 0, None),
        ("uint8", 2, 2, None),
        ("int8", 3, 1, None),
        ("uint8", 4, 2, None),
        ("int16", 5, 3, None),
        ("uint16", 6, 4, None),
        ("int32", 7, 5, None),
        ("uint32", 8, 6, None),
        ("string", 9, 11, None),
        ("int64", 10, 14, "urn:xjiang-spec:property:int-d:0000000a:xjiang-wb28:1"),
        ("float", 11, 15, "urn:xjiang-spec:property:float-one-uint-b:0000000b:xjiang-wb28:1"),
        ("float", 12, 16, "urn:xjiang-spec:property:float-two-uint-b:0000000c:xjiang-wb28:1"),
        ("float", 13, 17, "urn:xjiang-spec:property:float-one-uint-c:0000000d:xjiang-wb28:1"),
        ("float", 14, 18, "urn:xjiang-spec:property:float-two-uint-c:0000000e:xjiang-wb28:1"),
        ("float", 15, 19, "urn:xjiang-spec:property:float-one-int-b:0000000f:xjiang-wb28:1"),
        ("float", 16, 20, "urn:xjiang-spec:property:float-two-int-b:00000010:xjiang-wb28:1"),
        ("float", 17, 21, "urn:xjiang-spec:property:float-one-int-c:00000011:xjiang-wb28:1"),
        ("float", 18, 22, "urn:xjiang-spec:property:float-two-int-c:00000012:xjiang-wb28:1"),
        ("int64", 19, 14, "urn:xjiang-spec:property:array:00000013:xjiang-wb28:1"),
    ]
    props = []
    for fmt, iid, _typeid, urn in base:
        item = {
            "format": fmt,
            "access": ["read", "notify", "write"],
            "iid": iid,
            "comment": f"p{iid}",
        }
        if urn:
            item["type"] = urn
        props.append(item)
    return props


def _data_service_props():
    return [
        {"format": "int64", "access": ["read", "notify", "write"], "iid": 10,
         "type": "urn:xjiang-spec:property:int-d:0000000a:xjiang-wb28:1", "comment": "字符串"},
        {"format": "float", "access": ["read", "notify", "write"], "value-range": [0, 6553.5, 0.1], "iid": 11,
         "type": "urn:xjiang-spec:property:float-one-uint-b:0000000b:xjiang-wb28:1", "comment": "f1u16"},
        {"format": "float", "access": ["read", "notify", "write"], "value-range": [0, 655.35, 0.01], "iid": 12,
         "type": "urn:xjiang-spec:property:float-two-uint-b:0000000c:xjiang-wb28:1", "comment": "f2u16"},
        {"format": "float", "access": ["read", "notify", "write"], "value-range": [0, 429496729.5, 0.1], "iid": 13,
         "type": "urn:xjiang-spec:property:float-one-uint-c:0000000d:xjiang-wb28:1", "comment": "f1u32"},
        {"format": "float", "access": ["read", "notify", "write"], "value-range": [0, 42949672.95, 0.01], "iid": 14,
         "type": "urn:xjiang-spec:property:float-two-uint-c:0000000e:xjiang-wb28:1", "comment": "f2u32"},
        {"format": "float", "access": ["read", "notify", "write"], "value-range": [-3276.8, 3276.7, 0.1], "iid": 15,
         "type": "urn:xjiang-spec:property:float-one-int-b:0000000f:xjiang-wb28:1", "comment": "f1i16"},
        {"format": "float", "access": ["read", "notify", "write"], "value-range": [-327.68, 327.67, 0.01], "iid": 16,
         "type": "urn:xjiang-spec:property:float-two-int-b:00000010:xjiang-wb28:1", "comment": "f2i16"},
        {"format": "float", "access": ["read", "notify", "write"], "value-range": [-214748364.8, 214748364.7, 0.1], "iid": 17,
         "type": "urn:xjiang-spec:property:float-one-int-c:00000011:xjiang-wb28:1", "comment": "f1i32"},
        {"format": "float", "access": ["read", "notify", "write"], "value-range": [-21474836.48, 21474836.47, 0.01], "iid": 18,
         "type": "urn:xjiang-spec:property:float-two-int-c:00000012:xjiang-wb28:1", "comment": "f2i32"},
        {"format": "int64", "access": ["read", "notify", "write"], "value-range": [0, 999999999999999, 1], "iid": 19,
         "type": "urn:xjiang-spec:property:array:00000013:xjiang-wb28:1", "comment": "array"},
    ]


def test_wb28_xjiang_typeids_match_reference_mapping():
    bundle = parse_function_json({
        "services": [{
            "iid": 3,
            "type": "urn:xjiang-spec:service:data:00007801:xjiang-wb28:1",
            "properties": _data_service_props(),
        }],
    })
    by_piid = {
        int(meta["source_piid"]): int(meta["typeid"])
        for meta in bundle["attributes"].values()
        if meta.get("source_siid") == 3
    }
    assert by_piid[10] == 0x0E
    assert by_piid[11] == 0x0F
    assert by_piid[12] == 0x10
    assert by_piid[13] == 0x11
    assert by_piid[14] == 0x12
    assert by_piid[15] == 0x13
    assert by_piid[16] == 0x14
    assert by_piid[17] == 0x15
    assert by_piid[18] == 0x16
    assert by_piid[19] == 0x0E


def test_wb28_dev_info_matches_reference_expand_rules():
    wb28 = parse_function_json(WB28_FIXTURE.read_text(encoding="utf-8"))
    bundle = parse_function_json({
        "services": [{
            "iid": 3,
            "type": "urn:xjiang-spec:service:data:00007801:xjiang-wb28:1",
            "comment": "数据类型",
            "properties": _full_data_service_props(),
            "actions": [
                {"in": [], "out": [], "iid": 1,
                 "type": "urn:xjiang-spec:action:action-unparam:00002801:xjiang-wb28:1", "comment": "方法下发不带参"},
                {"in": [1], "out": [1], "iid": 2,
                 "type": "urn:xjiang-spec:action:action-param:00002802:xjiang-wb28:1", "comment": "方法下发带参"},
            ],
            "events": [
                {"arguments": [], "iid": 1,
                 "type": "urn:xjiang-spec:event:event-unparam:00005001:xjiang-wb28:1", "comment": "事件上报不带参"},
                {"arguments": [4, 1], "iid": 2,
                 "type": "urn:xjiang-spec:event:event-param:00005002:xjiang-wb28:1", "comment": "事件上报带参"},
            ],
        }],
    })
    user = build_product_cfg(
        product_name="wb28",
        pid="11747",
        model="xjiang.curtain.wb28",
        attributes=bundle["attributes"],
        actions=bundle["actions"],
        events=bundle["events"],
        mcu_version="1.0.0",
    )
    user["source_function_json"] = WB28_FIXTURE.read_text(encoding="utf-8")
    user["product_info"]["device_info_version"] = [0, 0, 9]
    cfg = merge_protocol(load_protocol(ROOT / "product" / "v3_serial.json"), user)
    data = build_dev_info_data(cfg)
    assert data == REFERENCE_DEV_INFO_DATA, data.hex(" ")

    frame = encode_dev_info_frame(cfg)
    split = split_frame(frame, cfg)
    assert split.cmd_code == 0x21
    assert len(split.data) == len(REFERENCE_DEV_INFO_DATA)


def test_snapshot_display_separates_attr_name_and_value():
    entry = SimpleNamespace(
        cn_name="数据类型-uint32",
        name="uint32",
        typeid=6,
        enum={},
        unit="",
    )

    class _Center:
        def get_entry(self, attrid):
            return entry

        def resolve_wire_attrid(self, wire_id):
            return wire_id

        def validate_attr_value(self, attrid, value):
            return value

    field = {"attrid": "0x07", "typeid": 6, "value": 15, "value_raw": "15"}
    text = _format_attr_semantics(field, _Center(), snapshot_style=True)
    assert "uint32：15" in text
    assert "uint3215" not in text
