"""Boundary tests: product-switch generation discard & RawDataWriter one-shot error."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from protocol_parser.attr_center import AttrStateCenter
from protocol_parser.storage import RawDataWriter
from protocol_parser.ui_helpers import _format_attr_semantics


def _minimal_cfg(attrs: dict) -> dict:
    return {
        "attributes": attrs,
        "frame": {"header": "0xA5A5", "version": 3},
    }


def test_generation_stale_callback_discard() -> None:
    """After load_product bumps generation, a stale async write is ignored."""
    center = AttrStateCenter()
    cfg_a = _minimal_cfg({
        "0x01": {
            "name": "power",
            "cn_name": "开关",
            "typeid": 0,
            "access": "读写",
            "initial_value": False,
        },
        "0x02": {
            "name": "mode",
            "cn_name": "模式",
            "typeid": 2,
            "access": "读写",
            "initial_value": 1,
        },
    })
    center.load_product(cfg_a)
    gen_a = center.generation
    assert gen_a >= 1
    center.set_attr_value(0x01, True)
    assert center.get_attr_value(0x01)[1] is True

    # Simulate product switch
    cfg_b = _minimal_cfg({
        "0x10": {
            "name": "temp",
            "cn_name": "温度",
            "typeid": 2,
            "access": "读写",
            "initial_value": 25,
        },
    })
    center.load_product(cfg_b)
    gen_b = center.generation
    assert gen_b > gen_a

    # Stale callback from product A tries to write old attrids
    result = SimpleNamespace(
        cmd_code=0x10,
        fields=[
            {
                "attrid": 0x01,
                "value": False,
                "typeid": 0,
            }
        ],
    )
    changed = center.update_from_frame(result, expected_generation=gen_a)
    assert changed == []
    # New product must not be polluted; 0x01 does not even exist now
    assert center.get_entry(0x01) is None
    assert center.get_attr_value(0x10)[1] == 25

    # Atomic write with stale generation is also discarded
    old = center.apply_values_atomic({0x10: 30}, expected_generation=gen_a)
    assert old == {}
    assert center.get_attr_value(0x10)[1] == 25

    # Fresh generation still works
    changed2 = center.update_from_frame(
        SimpleNamespace(
            cmd_code=0x10,
            fields=[{"attrid": 0x10, "value": 28, "typeid": 2}],
        ),
        expected_generation=gen_b,
    )
    assert 0x10 in changed2
    assert center.get_attr_value(0x10)[1] == 28


def test_one_shot_error_callback(tmp_path: Path) -> None:
    """Fatal write failures fire on_error only once; subsequent failures are silent."""
    errors: list[str] = []
    lock = threading.Lock()

    def on_error(message: str) -> None:
        with lock:
            errors.append(message)

    class AlwaysFailFile:
        def write(self, _data) -> int:
            raise OSError("disk full")

        def flush(self) -> None:
            return None

        def fileno(self) -> int:
            raise OSError("no descriptor")

        def close(self) -> None:
            return None

    writer = RawDataWriter(
        directory=tmp_path,
        basename="oneshot",
        batch_interval=0.01,
        queue_size=50,
        on_error=on_error,
        opener=lambda _path, _mode: AlwaysFailFile(),
    )
    writer.start()
    # Hammer the queue with many records; worker will hit OSError repeatedly
    for i in range(20):
        writer.enqueue(f"payload-{i}".encode(), time.time())
    # Give worker time to process and fail
    time.sleep(0.5)
    stats = writer.stop(drain=False, timeout=2.0)

    assert stats.failed
    assert "disk full" in writer.last_error
    # Exactly one on_error callback despite many failed writes
    assert len(errors) == 1
    assert "原始数据保存已停止" in errors[0]


def test_format_attr_semantics_logs_structure_errors(caplog) -> None:
    """AttributeError/KeyError inside formatter must be logged with exception."""

    class BrokenCenter:
        def resolve_wire_attrid(self, wire_id):
            raise AttributeError("missing _wire_to_internal")

        def get_entry(self, _id):
            return None

    with caplog.at_level(logging.ERROR):
        out = _format_attr_semantics(
            {"attrid": 0x01, "value": 1},
            BrokenCenter(),
        )
    # Fallback display still returned
    assert "线ID" in out or out == "" or "属性" in out
    # And a structure error was logged
    assert any(
        "structure error" in r.message or "AttributeError" in r.message
        for r in caplog.records
    )
