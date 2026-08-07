from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from protocol_parser.auto_reply import AutoReplyEngine
from protocol_parser.dev_info_encoder import _pid_to_uint32, encode_dev_info_frame
from protocol_parser.parser import HexParseError, ProtocolConfigError, parse_hex_input
from protocol_parser.product_importer import build_product_cfg


ROOT = Path(__file__).resolve().parents[1]


def test_pid_leading_zero_is_decimal() -> None:
    assert _pid_to_uint32("00123") == 123
    assert _pid_to_uint32("0x7B") == 123
    with pytest.raises(ProtocolConfigError):
        _pid_to_uint32("abc")


def test_leading_zero_pid_encodes_into_0x21_as_decimal_123() -> None:
    cfg = build_product_cfg(
        product_name="pid-leading-zero",
        pid="00123",
        model="test.model",
        attributes={},
        mcu_version="1.0.0",
    )
    frame = encode_dev_info_frame(cfg)
    assert bytes.fromhex("06 F7 00 00 00 7B") in frame


def test_hex_input_strict_whitelist_keeps_valid_inputs() -> None:
    assert parse_hex_input("A5 5A 03 20") == bytes.fromhex("A5 5A 03 20")
    assert parse_hex_input("0xA5,0x5A") == bytes.fromhex("A5 5A")
    with pytest.raises(HexParseError):
        parse_hex_input("0xZZ11")
    with pytest.raises(HexParseError):
        parse_hex_input("A5G5")


class _FakeAttrCenter:
    def __init__(self) -> None:
        self.cfg = {}
        self.validate_calls: dict[int, int] = {}
        self.writes: list[tuple[int, object]] = []
        self.entries = {
            1: SimpleNamespace(access="读写"),
            2: SimpleNamespace(access="读写"),
        }

    def get_frame_attr_records(self, result):
        return [(1, 1, 10), (2, 2, 20)]

    def get_entry(self, attrid: int):
        return self.entries.get(attrid)

    def validate_attr_value(self, attrid: int, value):
        count = self.validate_calls.get(attrid, 0) + 1
        self.validate_calls[attrid] = count
        # First validation succeeds for both attrs. During the mandatory second
        # preflight, attr 2 becomes invalid. No write is allowed before this.
        if attrid == 2 and count == 2:
            raise ValueError("second preflight rejected")
        return value

    def set_attr_value(self, attrid: int, value):
        self.writes.append((attrid, value))

    def reset_heartbeat_counter(self):
        return None


class _FakeCmd:
    def __init__(self) -> None:
        self.ack_count = 0

    def build_cmd_ack_resp(self, msg_id: int) -> bytes:
        self.ack_count += 1
        return b"ACK"

    def build_attr_report(self, attrids, values) -> bytes:
        return b"REPORT"


def test_auto_reply_second_preflight_prevents_partial_write_and_ack() -> None:
    ac = _FakeAttrCenter()
    cmd = _FakeCmd()
    engine = AutoReplyEngine(None, cmd, ac)
    result = SimpleNamespace(fields=[{"name": "msg_id", "value": 7}])
    frame = SimpleNamespace(data=b"")
    with pytest.raises(ValueError, match="second preflight rejected"):
        engine._reply_cmd_dispatch(result, frame)
    assert ac.writes == []
    assert cmd.ack_count == 0
    assert ac.validate_calls == {1: 2, 2: 2}


def test_raw_writer_callbacks_are_bridged_through_qt_signals() -> None:
    gui = (ROOT / "protocol_parser" / "gui.py").read_text(encoding="utf-8")
    assert "storage_error_signal = Signal(str)" in gui
    assert "storage_drop_signal = Signal(int)" in gui
    assert "self.bridge.storage_error_signal.connect(self._on_storage_error)" in gui
    assert "self.bridge.storage_drop_signal.connect(self._on_storage_drop)" in gui
    assert "on_error=lambda message: self.bridge.storage_error_signal.emit(message)" in gui
    assert "on_drop=lambda count: self.bridge.storage_drop_signal.emit(count)" in gui
