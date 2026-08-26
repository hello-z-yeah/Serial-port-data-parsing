from __future__ import annotations

from types import SimpleNamespace

from protocol_parser.dev_info_encoder import build_dev_info_data
from protocol_parser.product_importer import parse_function_json
from protocol_parser.ui_helpers import _format_attr_semantics


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
