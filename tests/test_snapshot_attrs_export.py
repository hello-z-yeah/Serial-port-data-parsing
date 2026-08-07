import json
from pathlib import Path

from protocol_parser.attr_center import AttrStateCenter
from protocol_parser.auto_cmd import AutoCmdEngine
from protocol_parser.parser import load_protocol, merge_protocol, to_hex
from protocol_parser.product_importer import (
    build_product_cfg,
    parse_expand_rules,
    parse_function_json,
)

EXPECTED_SNAPSHOT = (
    "A5 A5 03 24 00 96 02 00 00 02 01 00 0B 02 00 0A "
    "68 65 6C 6C 6F 77 6F 72 6C 64 02 04 00 02 05 00 02 06 00 "
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


def _build_cfg(raw_text: str, root: Path) -> dict:
    source = json.loads(raw_text)
    info = parse_expand_rules(source["Base"]["expandRules"])
    user_cfg = build_product_cfg(
        product_name="test",
        pid=info["pid"],
        model=info["model"],
        attributes=parse_function_json(raw_text),
        mcu_version=source["Base"]["version"],
    )
    user_cfg["source_function_json"] = raw_text
    user_cfg["device_info_expand_rules"] = source["Base"]["expandRules"]
    cfg = merge_protocol(load_protocol(root / "product" / "v3_serial.json"), user_cfg)
    for key in (
        "product_info",
        "source_function_json",
        "device_info_expand_rules",
        "import_source",
    ):
        cfg[key] = user_cfg[key]
    return cfg


def test_attrs_export_snapshot_uses_serial_ids_and_now_values():
    root = Path(__file__).resolve().parents[1]
    raw_text = (root / "tests" / "fixtures" / "attribute_export.json").read_text(
        encoding="utf-8"
    )
    cfg = _build_cfg(raw_text, root)
    center = AttrStateCenter()
    center.load_product(cfg)
    assert center.get_attr_value(2)[1] == "helloworld"
    assert center.get_readable_attrs() == [0, 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 13, 14, 15, 25, 26, 27]
    assert to_hex(AutoCmdEngine(center).build_snapshot_resp()) == EXPECTED_SNAPSHOT
