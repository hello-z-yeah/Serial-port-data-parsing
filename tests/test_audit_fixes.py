from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from protocol_parser.action_event_importer import (
    lookup_action_event_label,
    resolve_action_event_entry,
)
from protocol_parser.product_importer import safe_protocol_filename
from protocol_parser.product_management import disambiguate_product_catalog
from protocol_parser.session_bridge import snapshot_from_app


def test_word_import_filename_is_sanitized():
    source = Path("protocol_parser/gui.py").read_text(encoding="utf-8")
    assert "safe_protocol_filename" in source
    assert 'save_path = get_protocol_dir() / safe_protocol_filename' in source
    assert safe_protocol_filename("..\\outside") == "outside.json"


def test_disambiguate_duplicate_json_product_names():
    catalog = disambiguate_product_catalog(
        [
            ("串口3.0协议", "__builtin_v3__", "word"),
            ("智秋温度计", str(Path("a.json")), "json"),
            ("智秋温度计", str(Path("b.json")), "json"),
            ("Word协议", str(Path("word.json")), "word"),
        ]
    )
    labels = [name for name, _, kind in catalog if kind == "json"]
    assert len(labels) == 2
    assert labels[0] != labels[1]
    assert all("智秋温度计" in label for label in labels)
    assert any("a.json" in label for label in labels)
    assert any("b.json" in label for label in labels)


def test_resolve_action_event_entry_prefers_full_entry_key():
    events = [
        {"kind": "event", "service_siid": 2, "service_iid": 1, "serial_id": 21, "cn_name": "A"},
        {"kind": "event", "service_siid": 3, "service_iid": 1, "serial_id": 21, "cn_name": "B"},
    ]
    picked = resolve_action_event_entry(events, entry=events[1])
    assert picked is not None
    assert picked["cn_name"] == "B"
    assert resolve_action_event_entry(events, wire_id=21) is None
    assert resolve_action_event_entry(events, wire_id=21, allow_ambiguous=True)["cn_name"] == "A"


def test_lookup_action_event_label_joins_ambiguous_names():
    cfg = {
        "events": [
            {"serial_id": 21, "cn_name": "过热"},
            {"serial_id": 21, "cn_name": "告警"},
        ]
    }
    assert lookup_action_event_label(cfg, "event", 21) == "过热 / 告警"


def test_session_snapshot_does_not_persist_json_product():
    app = SimpleNamespace(
        product_var="JSON产品A",
        _product_sources={"JSON产品A": "demo.json"},
        _product_kinds={"JSON产品A": "json"},
    )
    app.monitor_page = None

    snap = snapshot_from_app(app)
    assert snap.product_name == ""
    assert snap.product_source == ""


def test_session_snapshot_keeps_word_product():
    app = SimpleNamespace(
        product_var="Word协议A",
        _product_sources={"Word协议A": "word.json"},
        _product_kinds={"Word协议A": "word"},
    )
    app.monitor_page = None

    snap = snapshot_from_app(app)
    assert snap.product_name == "Word协议A"
    assert snap.product_source == "word.json"


def test_mcu_auto_reply_rebinds_after_product_or_toggle():
    gui = Path("protocol_parser/gui.py").read_text(encoding="utf-8")
    mcu = Path("protocol_parser/mcu_page.py").read_text(encoding="utf-8")
    assert "def rebind_mcu_auto_reply_session" in gui
    assert "self.rebind_mcu_auto_reply_session()" in gui
    assert "if self._is_mcu_auto_reply_context_active():" in gui
    assert "self._mw.rebind_mcu_auto_reply_session()" in mcu
