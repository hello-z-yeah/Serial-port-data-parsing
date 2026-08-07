from __future__ import annotations

import json

from protocol_parser.dev_info_encoder import encode_dev_info_frame
from protocol_parser.product_importer import (
    build_product_cfg,
    extract_device_info_metadata,
)


CORRECT_FRAME_HEX = (
    "A5 A5 03 21 00 BA 00 00 09 "
    "06 F7 00 00 7F F1 "
    "0B F5 00 13 6E 76 63 73 6D 74 2E 6C 69 67 68 74 2E 77 6D 73 31 30 31 "
    "0E F3 00 96 "
    "02 00 0B 00 00 01 02 01 0B 00 00 02 06 02 0B 00 00 03 "
    "06 03 0B 00 00 04 06 04 0B 00 00 05 06 05 0B 00 00 06 "
    "0B 06 0B 00 00 07 0B 07 0B 00 00 08 "
    "02 08 04 00 00 01 00 09 04 00 00 02 0B 0A 04 00 00 03 "
    "0B 0B 04 00 00 04 01 0C 04 00 00 05 00 0D 04 00 00 06 "
    "00 0E 04 00 00 07 00 0F 0D 00 00 01 02 10 0D 00 00 02 "
    "00 11 0C 00 00 01 02 12 0C 00 00 02 "
    "00 13 02 00 00 01 04 14 02 00 00 03 02 15 02 00 00 02 "
    "06 16 02 00 00 04 02 17 02 00 00 05 0E 18 02 01 00 01 E0"
)
CORRECT_FRAME = bytes.fromhex(CORRECT_FRAME_HEX)
CORRECT_EXPAND_RULES = CORRECT_FRAME[9:-1].hex(" ").upper()


def _minimal_cfg(source_function_json: object) -> dict:
    # Deliberately use a stale UI version and only two selected attributes.
    # The exact exported Base metadata must remain authoritative for 0x21.
    cfg = build_product_cfg(
        product_name="test",
        pid="1",
        model="wrong.model",
        attributes={
            "0x00": {"name": "a", "cn_name": "A", "typeid": 0, "access": "读写"},
            "0x01": {"name": "b", "cn_name": "B", "typeid": 2, "access": "只读"},
        },
        mcu_version="1.0.0",
    )
    cfg["source_function_json"] = source_function_json
    return cfg


def test_exported_expand_rules_and_base_version_are_byte_exact() -> None:
    source = {
        "Base": {
            "version": [0, 0, 9],
            "expandRules": CORRECT_EXPAND_RULES,
        },
        # The service order intentionally does not match the F3 serial order.
        # Rebuilding from this list would produce the historical bad frame.
        "services": [
            {"iid": 2, "properties": [{"iid": 1, "format": "bool"}]},
            {"iid": 11, "properties": [{"iid": 1, "format": "uint8"}]},
        ],
    }
    assert encode_dev_info_frame(_minimal_cfg(json.dumps(source))) == CORRECT_FRAME


def test_existing_double_encoded_saved_product_is_recovered() -> None:
    source = {
        "data": {
            "base": {
                "mcu_version": "0.0.9",
                "expand_rules": CORRECT_EXPAND_RULES.lower(),
            }
        }
    }
    double_encoded = json.dumps(json.dumps(source))
    metadata = extract_device_info_metadata(double_encoded)
    assert metadata["version"] == [0, 0, 9]
    assert metadata["expand_rules"] == CORRECT_EXPAND_RULES
    assert encode_dev_info_frame(_minimal_cfg(double_encoded)) == CORRECT_FRAME


def test_explicit_metadata_remains_exact_after_attribute_selection() -> None:
    source = {"Base": {"version": [0, 0, 9], "expandRules": CORRECT_EXPAND_RULES}}
    cfg = _minimal_cfg(json.dumps(source))
    cfg["device_info_expand_rules"] = CORRECT_EXPAND_RULES
    cfg["product_info"]["device_info_version"] = [0, 0, 9]
    cfg["attributes"] = {"0x01": cfg["attributes"]["0x01"]}
    assert encode_dev_info_frame(cfg) == CORRECT_FRAME
