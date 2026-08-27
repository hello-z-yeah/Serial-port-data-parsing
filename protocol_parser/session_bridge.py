"""Map GUI preference state to the durable session snapshot.

The bridge deliberately restores preferences only.  It never opens a serial
port, starts cyclic transmission, or opens a data file during application
startup.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .session_snapshot import SessionSnapshot


def _text(widget: Any, default: str = "") -> str:
    if widget is None:
        return default
    getter = getattr(widget, "text", None)
    if not callable(getter):
        getter = getattr(widget, "toPlainText", None)
    if not callable(getter):
        return default
    try:
        return str(getter())
    except Exception:
        return default


def snapshot_from_app(app: Any) -> SessionSnapshot:
    product_name = str(getattr(app, "product_var", "") or "")
    product_sources = getattr(app, "_product_sources", {})
    product_kinds = getattr(app, "_product_kinds", {}) or {}
    if str(product_kinds.get(product_name) or "").strip().lower() == "json":
        product_name = ""
        product_source = ""
    else:
        product_source = str(product_sources.get(product_name, "") or "")
    monitor_page = getattr(app, "monitor_page", None)
    extras: dict[str, Any] = {}
    exporter = getattr(monitor_page, "export_settings", None)
    if callable(exporter):
        try:
            extras["monitor"] = exporter()
        except Exception:
            pass
    return SessionSnapshot(
        was_collecting=bool(getattr(app, "is_collecting", False)),
        port=str(getattr(app, "port_var", "") or ""),
        baudrate=int(str(getattr(app, "baudrate_var_last_valid", "9600"))),
        bytesize=int(_text(getattr(app, "bytesize_combo", None), "8")),
        stopbits=float(_text(getattr(app, "stopbits_combo", None), "1")),
        product_name=product_name,
        product_source=product_source,
        is_hex_format=bool(getattr(app, "hex_format", False)),
        direction=str(getattr(app, "serial_sender", "") or ""),
        detail_mode=bool(getattr(app, "detail_mode", False)),
        log_path=str(getattr(app, "log_path", "") or ""),
        save_raw_enabled=bool(getattr(app, "save_raw_enabled", False)),
        save_raw_path=_text(
            getattr(app, "save_path_edit", None),
            str(getattr(app, "save_raw_path", "") or ""),
        ),
        save_raw_filename=_text(
            getattr(app, "save_name_edit", None),
            str(getattr(app, "save_raw_filename", "") or ""),
        ),
        tx_send_mode=str(getattr(app, "send_mode", "protocol") or "protocol"),
        tx_cmd_code=str(getattr(app, "tx_cmd_code", "") or ""),
        tx_direction=str(getattr(app, "tx_direction", "") or ""),
        tx_fields_json=_text(
            getattr(app, "fields_edit", None),
            str(getattr(app, "tx_fields", "") or ""),
        ),
        tx_raw=_text(
            getattr(app, "raw_edit", None),
            str(getattr(app, "tx_raw", "") or ""),
        ),
        tx_cycle_enabled=bool(getattr(app, "tx_cycle", False)),
        tx_interval_ms=int(getattr(app, "tx_interval_ms", 1000)),
        extras=extras,
    )


def _select_port(combo: Any, port: str) -> bool:
    if combo is None or not port:
        return False
    normalized = port.split(" - ", 1)[0].strip().casefold()
    try:
        for index in range(combo.count()):
            candidate = str(combo.itemText(index) or "")
            candidate_port = candidate.split(" - ", 1)[0].strip().casefold()
            if candidate.casefold() == port.casefold() or candidate_port == normalized:
                combo.setCurrentIndex(index)
                return True
    except Exception:
        return False
    return False


def apply_snapshot_to_app(app: Any, snapshot: SessionSnapshot) -> None:
    """Restore safe preferences without starting monitoring or cyclic send."""
    _select_port(getattr(app, "port_combo", None), snapshot.port)

    baud = str(snapshot.baudrate if snapshot.baudrate > 0 else 9600)
    baud_combo = getattr(app, "baud_combo", None)
    if baud_combo is not None:
        baud_combo.setCurrentText(baud)
    app.baudrate_var = baud
    app.baudrate_var_last_valid = baud

    bytesize_combo = getattr(app, "bytesize_combo", None)
    if bytesize_combo is not None and snapshot.bytesize in {5, 6, 7, 8}:
        bytesize_combo.setCurrentText(str(snapshot.bytesize))
    stopbits_combo = getattr(app, "stopbits_combo", None)
    if stopbits_combo is not None and snapshot.stopbits in {1.0, 1.5, 2.0}:
        stopbits_combo.setCurrentText(f"{snapshot.stopbits:g}")

    if snapshot.product_name:
        product_name = str(snapshot.product_name or "").strip()
        kinds = getattr(app, "_product_kinds", {}) or {}
        if product_name and kinds.get(product_name, "word") != "json":
            loader = getattr(app, "load_product_cfg", None)
            if not callable(loader):
                loader = getattr(app, "_load_product_cfg", None)
            if callable(loader):
                try:
                    if not loader(product_name):
                        status = getattr(app, "_set_status", None)
                        if callable(status):
                            status(f"上次 Word 协议“{product_name}”恢复失败")
                except Exception as exc:
                    reporter = getattr(app, "report_error", None)
                    if callable(reporter):
                        reporter("会话恢复失败", exc)
                    else:
                        status = getattr(app, "_set_status", None)
                        if callable(status):
                            status(f"上次 Word 协议“{product_name}”恢复失败")

    mcu_page = getattr(app, "mcu_page", None)
    if mcu_page is not None:
        try:
            mcu_page.sync_products(preferred="")
        except Exception:
            pass

    hex_button = getattr(app, "btn_hex", None)
    if hex_button is not None:
        hex_button.blockSignals(True)
        hex_button.setChecked(bool(snapshot.is_hex_format))
        hex_button.blockSignals(False)
    on_hex = getattr(app, "_on_hex_toggled", None)
    if callable(on_hex):
        on_hex(bool(snapshot.is_hex_format))

    direction = snapshot.direction if snapshot.direction in {"模组发送", "MCU发送"} else "模组发送"
    app.serial_sender = direction
    app.tx_direction = (
        snapshot.tx_direction
        if snapshot.tx_direction in {"模组发送", "MCU发送"}
        else direction
    )
    sender_switch = getattr(app, "sender_switch", None)
    if sender_switch is not None:
        sender_switch.setValue(direction, emit_signal=False)

    app.detail_mode = bool(snapshot.detail_mode)
    more_button = getattr(app, "btn_more_config", None)
    if more_button is not None:
        more_button.blockSignals(True)
        more_button.setChecked(app.detail_mode)
        more_button.blockSignals(False)
    on_more = getattr(app, "_on_more_config_toggled", None)
    if callable(on_more):
        on_more(app.detail_mode)

    app.log_path = Path(snapshot.log_path) if snapshot.log_path else None
    app.save_raw_enabled = bool(snapshot.save_raw_enabled)
    app.save_raw_path = snapshot.save_raw_path or str(getattr(app, "save_raw_path", "") or "")
    app.save_raw_filename = snapshot.save_raw_filename or str(
        getattr(app, "save_raw_filename", "") or ""
    )
    save_path_edit = getattr(app, "save_path_edit", None)
    if save_path_edit is not None:
        save_path_edit.setText(app.save_raw_path)
    save_name_edit = getattr(app, "save_name_edit", None)
    if save_name_edit is not None:
        save_name_edit.setText(app.save_raw_filename)
    refresh_storage = getattr(app, "_refresh_save_raw_button", None)
    if callable(refresh_storage):
        refresh_storage()

    mode = snapshot.tx_send_mode
    if mode not in {"protocol", "raw_hex", "raw_ascii"}:
        mode = "protocol"
    set_mode = getattr(app, "_set_send_mode", None)
    if callable(set_mode):
        set_mode(mode)
    app.tx_cmd_code = snapshot.tx_cmd_code or str(getattr(app, "tx_cmd_code", "") or "")
    app.tx_fields = snapshot.tx_fields_json
    app.tx_raw = snapshot.tx_raw
    fields_edit = getattr(app, "fields_edit", None)
    if fields_edit is not None:
        fields_edit.setPlainText(snapshot.tx_fields_json)
    raw_edit = getattr(app, "raw_edit", None)
    if raw_edit is not None:
        raw_edit.setPlainText(snapshot.tx_raw)
    interval = min(3_600_000, max(10, int(snapshot.tx_interval_ms)))
    app.tx_interval_ms = interval
    interval_spin = getattr(app, "interval_spin", None)
    if interval_spin is not None:
        interval_spin.setValue(interval)

    # Persisted activity is diagnostic state only; startup must remain passive.
    app.tx_cycle = False
    cycle_button = getattr(app, "btn_cycle", None)
    if cycle_button is not None:
        cycle_button.blockSignals(True)
        cycle_button.setChecked(False)
        cycle_button.blockSignals(False)

    monitor_settings = snapshot.extras.get("monitor") if isinstance(snapshot.extras, dict) else None
    monitor_page = getattr(app, "monitor_page", None)
    importer = getattr(monitor_page, "import_settings", None)
    if callable(importer) and isinstance(monitor_settings, dict):
        try:
            importer(monitor_settings, persist=False)
        except Exception:
            pass
