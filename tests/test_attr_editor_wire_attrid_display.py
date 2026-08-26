from __future__ import annotations

from pathlib import Path

from protocol_parser.attr_editor import compute_wire_attrid_display
from protocol_parser.product_importer import parse_function_json

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "wb28_miot_product.json"


def test_attr_editor_shows_wire_serial_ids_for_miot_product():
    bundle = parse_function_json(FIXTURE.read_text(encoding="utf-8"))
    attrs = bundle["attributes"]
    attr_state = {
        key: {
            **meta,
            "selected": int(meta.get("source_siid", 0) or 0) == 3,
        }
        for key, meta in attrs.items()
    }
    cfg = {
        "attributes": attrs,
        "source_function_json": FIXTURE.read_text(encoding="utf-8"),
    }
    display, active = compute_wire_attrid_display(cfg, attr_state)
    assert active is True
    assert display["0x51"] == "0x00"
    assert display["0x54"] == "0x01"
    assert display["0x31"] == "—"
