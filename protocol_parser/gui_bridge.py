"""Qt signal bridge between serial workers and the main UI thread."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class UiBridge(QObject):
    frame_signal = Signal(object, float)
    raw_signal = Signal(bytes, float)
    error_signal = Signal(str)
    collector_error_signal = Signal(int, str, str)
    tx_signal = Signal(bytes, float, object)
    status_signal = Signal(str)
    attr_updated_signal = Signal(object)
    mcu_data_signal = Signal(object, object, float, bool, bool)
    storage_error_signal = Signal(str)
    storage_drop_signal = Signal(int)
    collector_stopped_signal = Signal(int, object, object)
