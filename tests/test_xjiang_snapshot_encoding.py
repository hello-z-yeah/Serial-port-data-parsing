from __future__ import annotations

from protocol_parser.attr_center import AttrStateCenter
from protocol_parser.auto_cmd import AutoCmdEngine
from protocol_parser.parser import (
    XJIANG_VARINT_SNAPSHOT_DEFAULT,
    encode_xjiang_varint,
    is_xjiang_varint_meta,
    load_protocol,
    merge_protocol,
    to_hex,
)
from protocol_parser.product_importer import build_product_cfg, parse_function_json

REFERENCE_SNAPSHOT = (
    "A5 A5 03 24 00 64 00 00 00 02 01 00 01 02 80 02 03 00 03 04 80 00 "
    "04 05 00 00 05 06 80 00 00 00 06 07 00 00 00 00 0B 08 00 0A "
    "68 65 6C 6C 6F 77 6F 72 6C 64 0E 09 00 03 01 E2 40 0F 0A 00 00 "
    "10 0B 00 00 11 0C 00 00 00 00 12 0D 00 00 00 00 13 0E 80 00 14 "
    "0F 80 00 15 10 80 00 00 00 16 11 80 00 00 00 0E 12 00 03 01 E2 "
    "40 64"
)


def _xjiang_data_service_json() -> dict:
    props = [
        {"format": "bool", "access": ["read", "notify", "write"], "iid": 1, "comment": "bool"},
        {"format": "uint8", "access": ["read", "notify", "write"], "iid": 2, "comment": "mnum"},
        {"format": "int8", "access": ["read", "notify", "write"], "iid": 3, "comment": "int-a"},
        {"format": "uint8", "access": ["read", "notify", "write"], "iid": 4, "comment": "uint-a"},
        {"format": "int16", "access": ["read", "notify", "write"], "iid": 5, "comment": "int-b"},
        {"format": "uint16", "access": ["read", "notify", "write"], "iid": 6, "comment": "uint-b"},
        {"format": "int32", "access": ["read", "notify", "write"], "iid": 7, "comment": "int-c"},
        {"format": "uint32", "access": ["read", "notify", "write"], "iid": 8, "comment": "uint-c"},
        {"format": "string", "access": ["read", "notify", "write"], "iid": 9, "comment": "str"},
        {
            "format": "int64",
            "access": ["read", "notify", "write"],
            "iid": 10,
            "type": "urn:xjiang-spec:property:int-d:0000000a:xjiang-wb28:1",
            "comment": "int-d",
        },
        {
            "format": "float",
            "access": ["read", "notify", "write"],
            "value-range": [0, 6553.5, 0.1],
            "iid": 11,
            "type": "urn:xjiang-spec:property:float-one-uint-b:0000000b:xjiang-wb28:1",
            "comment": "float-one-uint-b",
        },
        {
            "format": "float",
            "access": ["read", "notify", "write"],
            "value-range": [0, 655.35, 0.01],
            "iid": 12,
            "type": "urn:xjiang-spec:property:float-two-uint-b:0000000c:xjiang-wb28:1",
            "comment": "float-two-uint-b",
        },
        {
            "format": "float",
            "access": ["read", "notify", "write"],
            "value-range": [0, 429496729.5, 0.1],
            "iid": 13,
            "type": "urn:xjiang-spec:property:float-one-uint-c:0000000d:xjiang-wb28:1",
            "comment": "float-one-uint-c",
        },
        {
            "format": "float",
            "access": ["read", "notify", "write"],
            "value-range": [0, 42949672.95, 0.01],
            "iid": 14,
            "type": "urn:xjiang-spec:property:float-two-uint-c:0000000e:xjiang-wb28:1",
            "comment": "float-two-uint-c",
        },
        {
            "format": "float",
            "access": ["read", "notify", "write"],
            "value-range": [-3276.8, 3276.7, 0.1],
            "iid": 15,
            "type": "urn:xjiang-spec:property:float-one-int-b:0000000f:xjiang-wb28:1",
            "comment": "float-one-int-b",
        },
        {
            "format": "float",
            "access": ["read", "notify", "write"],
            "value-range": [-327.68, 327.67, 0.01],
            "iid": 16,
            "type": "urn:xjiang-spec:property:float-two-int-b:00000010:xjiang-wb28:1",
            "comment": "float-two-int-b",
        },
        {
            "format": "float",
            "access": ["read", "notify", "write"],
            "value-range": [-214748364.8, 214748364.7, 0.1],
            "iid": 17,
            "type": "urn:xjiang-spec:property:float-one-int-c:00000011:xjiang-wb28:1",
            "comment": "float-one-int-c",
        },
        {
            "format": "float",
            "access": ["read", "notify", "write"],
            "value-range": [-21474836.48, 21474836.47, 0.01],
            "iid": 18,
            "type": "urn:xjiang-spec:property:float-two-int-c:00000012:xjiang-wb28:1",
            "comment": "float-two-int-b",
        },
        {
            "format": "int64",
            "access": ["read", "notify", "write"],
            "iid": 19,
            "type": "urn:xjiang-spec:property:array:00000013:xjiang-wb28:1",
            "comment": "array",
        },
    ]
    return {
        "services": [{
            "iid": 3,
            "type": "urn:xjiang-spec:service:data:00007801:xjiang-wb28:1",
            "properties": props,
        }],
    }


def _build_cfg():
    from pathlib import Path

    bundle = parse_function_json(_xjiang_data_service_json())
    user = build_product_cfg(
        product_name="xjiang-data-types",
        pid="1",
        model="test.model",
        attributes=bundle["attributes"],
    )
    root = Path(__file__).resolve().parents[1]
    return merge_protocol(load_protocol(root / "product" / "v3_serial.json"), user)


def test_xjiang_varint_helpers():
    meta = {"source_type_urn": "urn:xjiang-spec:property:int-d:0000000a:xjiang-wb28:1"}
    assert is_xjiang_varint_meta(meta)
    assert encode_xjiang_varint(123456).hex(" ") == "01 e2 40"
    assert encode_xjiang_varint(0) == b"\x00"
    assert XJIANG_VARINT_SNAPSHOT_DEFAULT == 123456


def test_xjiang_data_types_snapshot_matches_reference_tool():
    cfg = _build_cfg()
    center = AttrStateCenter()
    center.load_product(cfg)

    frame = AutoCmdEngine(center).build_snapshot_resp()
    assert to_hex(frame) == REFERENCE_SNAPSHOT


def test_attrs_export_int_d_and_array_get_xjiang_varint_markers():
    from protocol_parser.product_importer import parse_function_json

    bundle = parse_function_json({
        "Attrs": [
            {
                "serialId": 9,
                "attributeKey": "int-d",
                "attributeName": "int-d",
                "dataRwx": "r",
                "type": 14,
                "dataType": "int-d",
                "nowValue": 42,
            },
            {
                "serialId": 18,
                "attributeKey": "array",
                "attributeName": "array",
                "dataRwx": "r",
                "type": 14,
                "dataType": "array",
                "nowValue": 99,
            },
        ],
    })
    int_d = next(
        meta
        for key, meta in bundle["attributes"].items()
        if str(key).endswith("09") or meta.get("snapshot_wire_id") == 9
    )
    array_meta = next(
        meta
        for key, meta in bundle["attributes"].items()
        if meta.get("snapshot_wire_id") == 18
    )
    assert int_d["wire_value_format"] == "xjiang_varint"
    assert array_meta["wire_value_format"] == "xjiang_varint"
    assert int_d["initial_value"] == 42


def test_snapshot_respects_explicit_product_zero_for_signed_int():
    from protocol_parser.parser import encode_frame, _encode_attr_list

    cfg = _build_cfg()
    cfg["attributes"]["0x53"]["initial_value"] = 0
    center = AttrStateCenter()
    center.load_product(cfg)
    entry = center.get_entry(0x53)
    assert entry.initial_value_from_product is True
    assert center.get_snapshot_value_for_encode(0x53) == 0
    payload = _encode_attr_list(cfg, [(2, 0, 1)])
    assert payload.hex(" ") == "01 02 00"


def test_standard_array_still_uses_nul_string_encoding():
    from protocol_parser.parser import _encode_attr_list

    cfg = {
        "attributes": {
            "0x01": {
                "name": "tags",
                "typeid": 14,
                "format": "array",
            }
        }
    }
    payload = _encode_attr_list(cfg, [(1, ["a", "b"], 14)])
    assert payload.endswith(bytes.fromhex("61 00 62"))
