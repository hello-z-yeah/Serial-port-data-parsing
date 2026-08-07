from __future__ import annotations

import json
import queue
from pathlib import Path
from types import SimpleNamespace

import pytest

from protocol_parser.attr_center import AttrStateCenter
from protocol_parser.auto_cmd import AutoCmdEngine
from protocol_parser.auto_reply import AutoReplyEngine
from protocol_parser.dev_info_encoder import build_dev_info_data
from protocol_parser.parser import (
    EncodeFrameError,
    ProtocolConfigError,
    encode_frame,
    load_protocol,
    parse_frame,
    split_frame,
)
from protocol_parser.serial_collector import FrameSynchronizer, SerialCollector
from protocol_parser.session_snapshot import SessionSnapshot, load_snapshot, save_snapshot
from protocol_parser.storage import RawDataWriter

ROOT = Path(__file__).resolve().parents[1]


def base_cfg() -> dict:
    return load_protocol(ROOT / "product" / "v3_serial.json")


def attr_cfg() -> dict:
    cfg = base_cfg()
    cfg["attributes"] = {
        "0x01": {
            "name": "label",
            "cn_name": "文本",
            "typeid": 11,
            "access": "读写",
            "range": {"length": 20},
            "initial_value": "",
        },
        "0x02": {
            "name": "scaled",
            "cn_name": "缩放值",
            "typeid": 15,
            "access": "读写",
            "range": [0, 65535],
            "initial_value": 0,
        },
        "0x03": {
            "name": "items",
            "cn_name": "数组",
            "typeid": 14,
            "access": "读写",
            "initial_value": [],
        },
    }
    return cfg


def child_records(result) -> list[dict]:
    records: list[dict] = []
    for field in result.fields:
        records.extend(field.get("children") or [])
    return records


class Collector:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.running = True

    def send(self, data: bytes, metadata=None) -> int:  # noqa: ANN001
        del metadata
        payload = bytes(data)
        self.sent.append(payload)
        return len(payload)


def test_decoded_children_are_machine_values_not_display_text() -> None:
    cfg = attr_cfg()
    frame = encode_frame(
        0x10,
        cfg,
        direction="request",
        fields=[(0x01, "hello", 11), (0x02, 2.5, 15), (0x03, ["a", "b"], 14)],
    )
    result = parse_frame(frame, cfg, direction="response")
    records = {record["attrid"]: record for record in child_records(result)}

    assert records["0x01"]["value"] == "hello"
    assert records["0x01"]["value_raw"] == "'hello'"
    assert records["0x02"]["value"] == pytest.approx(2.5)
    assert records["0x02"]["value_wire"] == 25
    assert records["0x03"]["value"] == ["a", "b"]


def test_string_command_auto_reply_ack_and_report() -> None:
    cfg = attr_cfg()
    center = AttrStateCenter()
    center.load_product(cfg)
    collector = Collector()
    reply = AutoReplyEngine(collector, AutoCmdEngine(center), center)
    reply.enable(True, enable_all_rules=True)

    command = AutoCmdEngine(center).build_cmd_send(7, 0x01, "hello")
    result = parse_frame(command, cfg, direction="request")
    sent = reply.on_frame(result, split_frame(command, cfg), 0.0)

    assert sent == 2
    assert [split_frame(item, cfg).cmd_code for item in collector.sent] == [0x01, 0x10]
    assert center.get_attr_value(0x01)[1] == "hello"


def test_bool_overflow_and_unknown_checksum_are_not_silent() -> None:
    cfg = base_cfg()
    cfg["attributes"] = {"0x01": {"name": "flag", "typeid": 0, "access": "读写"}}
    with pytest.raises(EncodeFrameError):
        encode_frame(0x10, cfg, direction="request", fields=[(0x01, 300, 0)])

    broken = json.loads(json.dumps(cfg))
    broken["frame"]["checksum"]["algorithm"] = "not-a-checksum"
    with pytest.raises(EncodeFrameError):
        encode_frame(0x20, broken, direction="request", fields={"value": 1})


def test_float_nan_and_inf_do_not_crash_parse() -> None:
    cfg = base_cfg()
    cfg["attributes"] = {"0x01": {"name": "f", "typeid": 9, "access": "读写"}}
    for value in (float("nan"), float("inf"), float("-inf")):
        frame = encode_frame(0x10, cfg, direction="request", fields=[(0x01, value, 9)])
        result = parse_frame(frame, cfg, direction="response")
        assert result.error is None
        assert child_records(result)


def test_group_roundtrip_uses_group_header_without_generic_length() -> None:
    cfg = base_cfg()
    cfg["attributes"] = {"0x23": {"name": "group", "typeid": 23, "access": "读写"}}
    group = {
        "packet_sum": 2,
        "packet_id": 1,
        "unit_sum": 2,
        "unit_num": 2,
        "unit_len": 2,
        "units": [
            {"unit_id": 1, "data": b"ab"},
            {"unit_id": 2, "data": b"cd"},
        ],
    }
    frame = encode_frame(0x10, cfg, direction="request", fields=[(0x23, group, 23)])
    wire = split_frame(frame, cfg).data
    assert wire[:2] == bytes([23, 0x23])
    assert wire[2:7] == bytes([2, 1, 2, 2, 2])
    result = parse_frame(frame, cfg, direction="response")
    record = child_records(result)[0]
    assert record["value"]["packet_id"] == 1
    assert [unit["data"] for unit in record["value"]["units"]] == [b"ab", b"cd"]


def test_frame_synchronizer_recovers_true_frame_after_bad_candidate() -> None:
    cfg = base_cfg()
    valid = encode_frame(0x20, cfg, direction="request", fields={"value": 1})
    bad = bytearray(valid)
    bad[-1] ^= 0x55
    sync = FrameSynchronizer(cfg)
    frames = sync.feed(bytes(bad) + valid)
    assert len(frames) == 1
    assert frames[0].raw == valid
    assert sync.error_count >= 1


def test_split_frame_rejects_trailing_bytes_but_sync_handles_two_frames() -> None:
    cfg = base_cfg()
    first = encode_frame(0x20, cfg, direction="request", fields={"value": 1})
    second = encode_frame(0x20, cfg, direction="request", fields={"value": 2})
    with pytest.raises(Exception):
        split_frame(first + second, cfg)
    assert [f.raw for f in FrameSynchronizer(cfg).feed(first + second)] == [first, second]


def test_odd_hex_is_rejected_without_rewriting_payload() -> None:
    collector = SerialCollector(cfg={}, port="COM_TEST")
    collector.running = True
    collector._serial = SimpleNamespace(is_open=True)
    collector._tx_queue = queue.Queue()
    with pytest.raises(EncodeFrameError):
        collector.send("A5 A5 3")
    assert collector._tx_queue.empty()


def test_low_power_service_uses_d1_and_low_power_state_clears() -> None:
    cfg = base_cfg()
    center = AttrStateCenter()
    center.load_product(cfg)
    engine = AutoCmdEngine(center)
    frame = engine.build_low_power_service(True)
    assert split_frame(frame, cfg).data == bytes([0x02, 0xD1, 0x01])

    collector = Collector()
    reply = AutoReplyEngine(collector, engine, center)
    reply._reply_module_status_ack(
        SimpleNamespace(fields=[{"name": "模组工作状态", "value": 5}]), None
    )
    assert reply.low_power_active is True
    reply._reply_module_status_ack(
        SimpleNamespace(fields=[{"name": "模组工作状态", "value": 0}]), None
    )
    assert reply.low_power_active is False


def test_attr_center_load_is_atomic_and_snapshots_do_not_leak_mutables() -> None:
    center = AttrStateCenter()
    good = attr_cfg()
    center.load_product(good)
    center.set_attr_value(0x03, ["x"])

    bad = attr_cfg()
    bad["attributes"]["0x01"] = {
        "name": "bad_bool",
        "cn_name": "非法布尔",
        "typeid": 0,
        "access": "读写",
        "enum": {"2": "非法值"},
        "initial_value": 2,
    }
    with pytest.raises(Exception):
        center.load_product(bad)

    assert center.cfg is good
    snapshot = center.get_entry(0x03)
    assert snapshot is not None
    snapshot.current_value.append("mutated")
    assert center.get_attr_value(0x03)[1] == ["x"]


def test_invalid_pid_is_not_replaced_with_crc32() -> None:
    cfg = base_cfg()
    cfg["product_info"] = {"pid": "not-a-pid", "model": "model", "mcu_version": "1.0.0"}
    with pytest.raises(Exception, match="PID"):
        build_dev_info_data(cfg)


def test_session_snapshot_whitelists_fields_and_types(tmp_path: Path) -> None:
    path = tmp_path / "session.json"
    save_snapshot(SessionSnapshot(baudrate=115200, port="COM7"), path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["close"] = "do not call"
    data["baudrate"] = "abc"
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = load_snapshot(path)
    assert not hasattr(loaded, "close")
    assert loaded.baudrate == 9600


def test_storage_basename_with_brackets_rotates_safely(tmp_path: Path) -> None:
    writer = RawDataWriter(
        directory=tmp_path,
        basename="capture[1]",
        max_file_bytes=1024,
        ascii_mode=False,
        queue_size=32,
    )
    writer.start()
    try:
        for index in range(8):
            assert writer.enqueue(bytes([index]) * 700, float(index))
    finally:
        stats = writer.stop(drain=True, timeout=3.0)
    assert stats.written_records == 8
    files = sorted(tmp_path.glob("*.dat"))
    assert len(files) >= 2
    assert all("capture[1]" in item.name for item in files)


def test_gui_review_fixes_are_present_statically() -> None:
    gui = (ROOT / "protocol_parser" / "gui.py").read_text(encoding="utf-8")
    mcu = (ROOT / "protocol_parser" / "mcu_page.py").read_text(encoding="utf-8")
    assert "def _commit_baud" in gui
    assert "self.baud_combo.editingFinished.connect" in gui
    assert "def _stop_tx_cycle" in gui
    assert "def _retry_stopping_collector" in gui
    assert "collector_error_signal = Signal(int, str, str)" in gui
    assert "self._auto_reply.reset_state()" in gui
    assert "self.btn_poweron_send_all.setEnabled(False)" in mcu
    assert "timer is not None and timer.isActive()" in mcu
    assert "QMessageBox.warning" not in mcu
    assert "QMessageBox.information" not in mcu


def test_scaled_decimal_encoding_never_falls_back_to_float32() -> None:
    cfg = base_cfg()
    cfg["attributes"] = {
        "0x01": {"name": "scaled", "typeid": 15, "access": "读写"}
    }
    frame = encode_frame(0x10, cfg, direction="request", fields=[(0x01, 25.3, 15)])
    data = split_frame(frame, cfg).data
    # typeid + attrid + UINT16 wire value 253; no unexpected four-byte float.
    assert data == bytes([15, 1, 0, 253])
    record = child_records(parse_frame(frame, cfg, direction="response"))[0]
    assert record["value"] == pytest.approx(25.3)
    assert record["value_wire"] == 253


def test_get_time_response_year_and_ota_formats_are_symmetric() -> None:
    cfg = base_cfg()
    time_frame = encode_frame(
        0x26,
        cfg,
        direction="response",
        fields={
            "errcode": 0,
            "timezone": 8,
            "year": 2025,
            "month": 8,
            "day": 7,
            "weekday": 4,
            "hour": 10,
            "minute": 5,
            "second": 6,
        },
    )
    assert split_frame(time_frame, cfg).data == bytes([0, 8, 25, 8, 7, 4, 10, 5, 6])
    ota_start = encode_frame(0x30, cfg, direction="request", fields={"ota_sign_type": 1})
    ota_verify = encode_frame(0x33, cfg, direction="request", fields={"sign_value": "AA BB"})
    assert split_frame(ota_start, cfg).data == b"\x01"
    assert split_frame(ota_verify, cfg).data == b"\xAA\xBB"


def test_unknown_type_and_empty_declared_type_are_reported_not_crashes() -> None:
    cfg = base_cfg()
    cfg["attributes"] = {
        "0x01": {"name": "custom", "typeid": 11, "declared_type": "bool", "access": "读写"}
    }
    # STRING typeid, attrid 1, zero two-byte length.
    frame = encode_frame(0x10, cfg, direction="request", data=bytes([11, 1, 0, 0]))
    result = parse_frame(frame, cfg, direction="response")
    messages = [str(field.get("text") or "") for field in result.fields]
    assert any("为空" in message for message in messages)

    unknown = encode_frame(0x10, cfg, direction="request", data=bytes([99, 1, 0]))
    result_unknown = parse_frame(unknown, cfg, direction="response")
    unknown_messages = [str(field.get("text") or "") for field in result_unknown.fields]
    assert any("不支持" in message for message in unknown_messages)


def test_auto_cmd_uses_wire_serial_id_in_both_write_and_get() -> None:
    cfg = base_cfg()
    cfg["attributes"] = {
        "0x41": {
            "name": "mapped",
            "cn_name": "映射属性",
            "typeid": 2,
            "access": "读写",
            "snapshot_wire_id": 3,
            "initial_value": 0,
        }
    }
    center = AttrStateCenter()
    center.load_product(cfg)
    engine = AutoCmdEngine(center)
    assert split_frame(engine.build_cmd_send(7, 0x41, 5), cfg).data == bytes([7, 2, 3, 5])
    assert split_frame(engine.build_get_attr_req(9, [0x41]), cfg).data == bytes([9, 3])


def test_write_timeout_drops_only_one_tx_request_without_stopping_rx() -> None:
    import protocol_parser.serial_collector as sc

    errors: list[str] = []

    class TimeoutSerial:
        is_open = True

        def write(self, payload: bytes) -> int:
            del payload
            raise sc.SerialTimeoutException("timeout")

    collector = SerialCollector(cfg={}, port="COM_TEST", on_error=errors.append)
    collector.running = True
    collector._serial = TimeoutSerial()
    collector._write_lock = __import__("threading").Lock()
    collector._tx_queue = queue.Queue()
    collector._tx_queue.put(sc.TxRequest(b"abc"))
    collector._tx_queue.put(sc._TX_STOP)
    collector._tx_loop()

    assert collector.running is True
    assert collector._stop_event.is_set() is False
    assert any("写超时" in message for message in errors)


def test_serial_error_classification_keeps_busy_and_missing_distinct() -> None:
    import protocol_parser.serial_collector as sc

    assert sc._classify_serial_error(PermissionError("Access is denied")) == "busy"
    assert sc._classify_serial_error(FileNotFoundError("not found")) == "not_found"
    assert "被占用" in sc._friendly_serial_error("COM7", PermissionError(), "busy")
    assert "已拔出" in sc._friendly_serial_error("COM7", FileNotFoundError(), "not_found")


def test_static_review_preserves_close_cleanup_and_screen_aware_minimum() -> None:
    gui = (ROOT / "protocol_parser" / "gui.py").read_text(encoding="utf-8")
    assert "fit_window_to_screen" in gui
    assert "self.setMinimumSize(1000, 640)" not in gui
    assert "super().closeEvent(event)" in gui
    assert "self._cmdlib_flush_pending_save()" in gui
    assert "collector.stop(timeout=3.0)" in gui
    assert "_layout_resize_timer" in gui
