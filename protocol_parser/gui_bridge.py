"""Qt signal bridge between serial workers and the main UI thread."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class UiBridge(QObject):
    receive_display_batch_signal = Signal(object)
    mcu_display_batch_signal = Signal(object)
    error_signal = Signal(str)
    collector_error_signal = Signal(int, str, str)
    tx_signal = Signal(bytes, float, object)
    status_signal = Signal(str)
    attr_updated_signal = Signal(object)
    mcu_frame_signal = Signal(int, object, object, float)
    storage_error_signal = Signal(str)
    storage_drop_signal = Signal(int)
    collector_stopped_signal = Signal(int, object, object)
    protocols_catalog_signal = Signal(object)
    # payload: (ports: list[dict], initial_startup: bool)
    ports_enumerated_signal = Signal(object)
