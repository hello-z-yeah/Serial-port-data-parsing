from __future__ import annotations

import queue
from pathlib import Path
from types import SimpleNamespace

import pytest

from protocol_parser.exceptions import ProductConfigError, SerialOperationError
from protocol_parser.monitor_settings import (
    DEFAULT_MAX_RECORD_LINES,
    load_monitor_settings,
    save_monitor_settings,
)
from protocol_parser.product_importer import _load_json_value
from protocol_parser.serial_collector import SerialCollector
from protocol_parser.session_bridge import apply_snapshot_to_app, snapshot_from_app
from protocol_parser.session_snapshot import SessionSnapshot


class _Combo:
    def __init__(self, items: list[str], current: str = "") -> None:
        self.items = list(items)
        self.current = current or (items[0] if items else "")

    def count(self) -> int:
        return len(self.items)

    def itemText(self, index: int) -> str:
        return self.items[index]

    def setCurrentIndex(self, index: int) -> None:
        self.current = self.items[index]

    def setCurrentText(self, text: str) -> None:
        self.current = text

    def text(self) -> str:
        return self.current


class _Text:
    def __init__(self, value: str = "") -> None:
        self.value = value

    def text(self) -> str:
        return self.value

    def toPlainText(self) -> str:
        return self.value

    def setText(self, value: str) -> None:
        self.value = value

    def setPlainText(self, value: str) -> None:
        self.value = value


class _Button:
    def __init__(self) -> None:
        self.checked = False

    def blockSignals(self, value: bool) -> None:
        return None

    def setChecked(self, value: bool) -> None:
        self.checked = bool(value)


class _Spin:
    def __init__(self) -> None:
        self.value = 0

    def setValue(self, value: int) -> None:
        self.value = value


class _SenderSwitch:
    def __init__(self) -> None:
        self.value = ""

    def setValue(self, value: str, *, emit_signal: bool = False) -> None:
        assert emit_signal is False
        self.value = value


def _fake_app() -> SimpleNamespace:
    app = SimpleNamespace(
        port_combo=_Combo(["COM3 - USB", "COM7 - UART"]),
        baud_combo=_Combo(["9600", "115200"]),
        bytesize_combo=_Combo(["5", "6", "7", "8"], "8"),
        stopbits_combo=_Combo(["1", "1.5", "2"], "1"),
        product_var="",
        _product_sources={"Demo": Path("Demo.json")},
        btn_hex=_Button(),
        btn_more_config=_Button(),
        sender_switch=_SenderSwitch(),
        save_path_edit=_Text(),
        save_name_edit=_Text(),
        fields_edit=_Text(),
        raw_edit=_Text(),
        interval_spin=_Spin(),
        btn_cycle=_Button(),
        monitor_page=None,
        save_raw_path="",
        save_raw_filename="",
        tx_cmd_code="0x20",
        tx_fields="",
        tx_raw="",
        send_mode="protocol",
        tx_cycle=True,
        is_collecting=False,
        log_path=None,
    )
    app.loaded_products = []
    app.load_product_cfg = lambda name: app.loaded_products.append(name) or True
    app._on_hex_toggled = lambda checked: setattr(app, "hex_format", bool(checked))
    app._on_more_config_toggled = lambda checked: setattr(app, "detail_mode", bool(checked))
    app._refresh_save_raw_button = lambda: None
    app._set_send_mode = lambda mode: setattr(app, "send_mode", mode)
    return app


def test_session_bridge_restores_preferences_without_starting_activity():
    app = _fake_app()
    snapshot = SessionSnapshot(
        was_collecting=True,
        port="COM7",
        baudrate=115200,
        bytesize=7,
        stopbits=1.5,
        product_name="Demo",
        is_hex_format=True,
        direction="MCU发送",
        detail_mode=True,
        save_raw_enabled=True,
        save_raw_path="D:/captures",
        save_raw_filename="session",
        tx_send_mode="raw_ascii",
        tx_fields_json='{"value": 2}',
        tx_raw="hello",
        tx_cycle_enabled=True,
        tx_interval_ms=250,
    )

    apply_snapshot_to_app(app, snapshot)

    assert app.port_combo.current == "COM7 - UART"
    assert app.baudrate_var_last_valid == "115200"
    assert app.loaded_products == ["Demo"]
    assert app.hex_format is True
    assert app.detail_mode is True
    assert app.send_mode == "raw_ascii"
    assert app.raw_edit.value == "hello"
    assert app.tx_cycle is False
    assert app.btn_cycle.checked is False
    assert not hasattr(app, "collector")

    captured = snapshot_from_app(app)
    assert captured.port == ""
    assert captured.baudrate == 115200
    assert captured.tx_cycle_enabled is False


def test_monitor_settings_round_trip_and_validation(tmp_path):
    path = tmp_path / "monitor.json"
    save_monitor_settings(
        {
            "pattern_text": "A5 A5",
            "is_hex": True,
            "hex_format": True,
            "autoscroll": False,
            "max_record_lines": 1_000_000,
        },
        path,
    )
    loaded = load_monitor_settings(path)
    assert loaded["pattern_text"] == "A5 A5"
    assert loaded["is_hex"] is True
    assert loaded["max_record_lines"] == 100_000

    path.write_text("{broken", encoding="utf-8")
    assert load_monitor_settings(path)["max_record_lines"] == DEFAULT_MAX_RECORD_LINES


def test_product_json_has_an_explicit_size_limit(monkeypatch):
    monkeypatch.setattr(
        "protocol_parser.product_importer.MAX_FUNCTION_JSON_CHARS",
        8,
    )
    with pytest.raises(ProductConfigError, match="不能超过"):
        _load_json_value('{"value": 123}')


def test_serial_collector_rejects_oversized_payload_before_queueing():
    collector = SerialCollector(cfg={}, port="COM1", max_tx_payload_bytes=4)
    collector.running = True
    collector._serial = SimpleNamespace(is_open=True)
    collector._tx_queue = queue.Queue(maxsize=2)

    with pytest.raises(SerialOperationError, match="不能超过"):
        collector.send(b"12345")
    assert collector._tx_queue.empty()


def test_monitor_export_uses_signal_for_thread_callback():
    root = Path(__file__).resolve().parents[1]
    source = (root / "protocol_parser" / "monitor_page.py").read_text(
        encoding="utf-8"
    )
    assert "_export_finished = Signal" in source
    assert "self._export_finished.emit(path, error)" in source
    assert "QTimer.singleShot" not in source[source.index("def _export_records") : source.index("def _on_export_finished")]


def test_monitor_page_source_has_bounded_records_and_hex_highlight():
    root = Path(__file__).resolve().parents[1]
    source = (root / "protocol_parser" / "monitor_page.py").read_text(
        encoding="utf-8"
    )
    assert "self.record_text.document().setMaximumBlockCount" in source
    assert 'highlighted = " ".join(f"{byte:02X}" for byte in pattern)' in source
    assert "def flush_pending_display(self)" in source


def test_choose_log_routes_through_active_page_widget():
    root = Path(__file__).resolve().parents[1]
    source = (root / "protocol_parser" / "gui.py").read_text(encoding="utf-8")
    region = source[source.index("def _choose_log") : source.index("def _setup_update_feature")]
    assert "_status_display_text_widget()" in region
    assert "flush_pending_display()" in region
