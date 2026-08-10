"""Serial port manager for lifecycle management of multiple collectors.

Manages registration, unregistration, and resource monitoring for serial ports
using OptimizedSerialCollector instances.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .exceptions import SerialOperationError, SerialStateError
from .serial_collector_optimized import OptimizedSerialCollector


class SerialManager:
    """Manages multiple serial port collectors with safe lifecycle."""

    def __init__(self):
        self._collectors: dict[str, OptimizedSerialCollector] = {}
        self._state_lock = threading.RLock()
        self._monitor_thread: threading.Thread | None = None
        self._monitor_stop = threading.Event()
        self._on_connection_changed: Callable[[str, str], None] | None = None

    def set_connection_callback(self, callback: Callable[[str, str], None]) -> None:
        """Set callback for connection state changes.

        callback(port_id: str, state: str) -> None
        state: 'connected' | 'disconnected' | 'error'
        """
        self._on_connection_changed = callback

    def register_port(
        self,
        port_id: str,
        cfg: dict,
        *,
        baudrate: int = 9600,
        bytesize: int = 8,
        stopbits: float = 1.0,
        direction: str | None = None,
        on_frame: Callable | None = None,
        on_raw: Callable | None = None,
        on_error: Callable | None = None,
        on_mcu_frame: Callable | None = None,
        on_connection_error: Callable | None = None,
        mcu_cfg: dict | None = None,
    ) -> OptimizedSerialCollector:
        """Register and start a serial port collector.

        Uses OptimizedSerialCollector with full callback arguments.
        Maps the logical ``port_id`` to the physical port via
        ``cfg["port"]`` when available, falling back to ``port_id``.
        """
        physical_port = (
            cfg.get("port", port_id) if isinstance(cfg, dict) else port_id
        )
        with self._state_lock:
            if port_id in self._collectors:
                existing = self._collectors[port_id]
                if existing.is_running():
                    raise SerialStateError(f"串口 {port_id} 已注册且正在运行")
            collector = OptimizedSerialCollector(
                cfg=cfg,
                port=physical_port,
                baudrate=baudrate,
                bytesize=bytesize,
                stopbits=stopbits,
                direction=direction,
                on_frame=on_frame,
                on_raw=on_raw,
                on_error=on_error,
                on_connection_error=on_connection_error,
                on_mcu_frame=on_mcu_frame,
                mcu_cfg=mcu_cfg,
            )
            self._collectors[port_id] = collector

        try:
            collector.start()
        except Exception:
            with self._state_lock:
                self._collectors.pop(port_id, None)
            raise

        self._notify_connection_changed(port_id, "connected")
        return collector

    def unregister_port(self, port_id: str) -> None:
        """Stop and unregister a serial port collector.

        Retrieves collector reference inside the lock, releases the lock,
        then calls collector.stop() outside the lock to prevent deadlocks
        with connection_changed callbacks.

        Raises SerialOperationError if the collector fails to stop cleanly.
        """
        with self._state_lock:
            collector = self._collectors.pop(port_id, None)

        if collector is None:
            return

        collector.stop()
        self._notify_connection_changed(port_id, "disconnected")

    def stop_port(self, port_id: str) -> None:
        """Stop a port's collector without removing it from the registry.

        Avoids holding self._state_lock while waiting for collector to stop.
        Raises SerialOperationError if the collector fails to stop cleanly.
        """
        with self._state_lock:
            collector = self._collectors.get(port_id)

        if collector is None:
            raise SerialStateError(f"串口 {port_id} 未注册")

        collector.stop()
        self._notify_connection_changed(port_id, "disconnected")

    def start_port(self, port_id: str) -> None:
        """Start a previously stopped port."""
        with self._state_lock:
            collector = self._collectors.get(port_id)

        if collector is None:
            raise SerialStateError(f"串口 {port_id} 未注册")

        collector.start()
        self._notify_connection_changed(port_id, "connected")

    def get_collector(self, port_id: str) -> OptimizedSerialCollector | None:
        with self._state_lock:
            return self._collectors.get(port_id)

    def list_ports(self) -> list[str]:
        with self._state_lock:
            return list(self._collectors.keys())

    def stop_all(self) -> None:
        """Stop all registered ports.

        Attempts to stop every port even if some fail, then raises the
        first SerialOperationError encountered (if any) after all ports
        have been processed.
        """
        with self._state_lock:
            port_ids = list(self._collectors.keys())

        first_error: SerialOperationError | None = None
        for port_id in port_ids:
            try:
                self.unregister_port(port_id)
            except SerialOperationError as exc:
                if first_error is None:
                    first_error = exc
            except Exception:
                pass

        if first_error is not None:
            raise first_error

    def _notify_connection_changed(self, port_id: str, state: str) -> None:
        callback = self._on_connection_changed
        if callback is None:
            return
        try:
            callback(port_id, state)
        except Exception:
            pass

    def start_resource_monitor(self, interval: float = 5.0) -> None:
        """Start background resource monitoring.

        The monitor checks port states periodically. It is robust against
        is_running evaluating to False initially (e.g., during startup
        transitions) by retrying instead of exiting prematurely.
        """
        with self._state_lock:
            if self._monitor_thread is not None and self._monitor_thread.is_alive():
                return
            self._monitor_stop.clear()
            self._monitor_thread = threading.Thread(
                target=self._resource_monitor_loop,
                args=(interval,),
                daemon=True,
                name="smst-resource-monitor",
            )
            self._monitor_thread.start()

    def stop_resource_monitor(self, *, timeout: float = 5.0) -> None:
        """Stop the resource monitor and wait for it to exit.

        Uses a shared timeout deadline to avoid permanent blocking.
        Guarantees the thread exits before clearing references, preventing
        leaks when start_resource_monitor is called immediately after.
        """
        with self._state_lock:
            monitor_thread = self._monitor_thread
            self._monitor_stop.set()

        if monitor_thread is not None:
            current = threading.current_thread()
            if monitor_thread is not current:
                deadline = time.monotonic() + max(0.0, float(timeout))
                monitor_thread.join(timeout=max(0.0, deadline - time.monotonic()))
                if monitor_thread.is_alive():
                    monitor_thread.join(timeout=max(0.0, deadline - time.monotonic()))

        with self._state_lock:
            self._monitor_thread = None

    def _resource_monitor_loop(self, interval: float) -> None:
        """Monitor resource usage of all registered collectors.

        Robust against collectors that are not yet running when the loop
        starts — treats them as 'starting' rather than exiting.
        Only fires 'disconnected' when BOTH running and connected are
        False (genuine disconnect, not transient startup window).
        """
        last_notified: dict[str, str] = {}
        while not self._monitor_stop.is_set():
            with self._state_lock:
                items = list(self._collectors.items())

            for port_id, collector in items:
                if self._monitor_stop.is_set():
                    break
                is_run = collector.is_running()
                is_conn = collector.is_connected()
                if not is_run and not is_conn:
                    state = "disconnected"
                elif is_run and not is_conn:
                    state = "disconnected"
                elif is_run and is_conn:
                    state = "connected"
                else:
                    state = "starting"

                prev = last_notified.get(port_id, "")
                if state != prev and state not in ("starting",):
                    self._notify_connection_changed(port_id, state)
                    last_notified[port_id] = state
                elif state == "starting":
                    pass

            self._monitor_stop.wait(timeout=interval)

    def get_status(self) -> dict:
        """Get status of all managed ports."""
        with self._state_lock:
            status = {}
            for port_id, collector in self._collectors.items():
                status[port_id] = {
                    "running": collector.is_running(),
                    "connected": collector.is_connected(),
                    "error_count": collector.get_error_count(),
                }
            return status


@dataclass
class SerialPortConfig:
    """Serializable configuration for a serial port registration.

    This is a convenience wrapper around the ``register_port`` arguments
    so that GUI or configuration-driven code can construct a port
    definition and pass it uniformly.
    """

    port: str = ""
    baudrate: int = 9600
    bytesize: int = 8
    stopbits: float = 1.0
    direction: str | None = None
    cfg: dict = field(default_factory=dict)
    mcu_cfg: dict | None = None
    primary_enabled: bool = True
    tx_queue_size: int = 1000
    max_reconnect_attempts: int = 5
    reconnect_delay: float = 1.0


@dataclass
class SerialPortStatus:
    """Snapshot of a port's current lifecycle state."""

    port_id: str
    running: bool = False
    connected: bool = False
    error_count: int = 0
    last_error: str = ""
    last_error_kind: str = ""

    @classmethod
    def from_collector(
        cls, port_id: str, collector: OptimizedSerialCollector
    ) -> "SerialPortStatus":
        return cls(
            port_id=port_id,
            running=collector.is_running(),
            connected=collector.is_connected(),
            error_count=collector.get_error_count(),
            last_error=collector.last_connection_error,
            last_error_kind=collector.last_connection_error_kind,
        )


class DistributedSerialManager(SerialManager):
    """Backward-compatible alias for SerialManager.

    Provided so code that imports ``DistributedSerialManager`` continues
    to work without modification.
    """

    def register_port_from_config(
        self,
        port_id: str,
        config: SerialPortConfig,
        *,
        on_frame: Callable | None = None,
        on_raw: Callable | None = None,
        on_error: Callable | None = None,
        on_mcu_frame: Callable | None = None,
        on_connection_error: Callable | None = None,
    ) -> OptimizedSerialCollector:
        """Register a port using a SerialPortConfig dataclass."""
        merged_cfg = dict(config.cfg)
        if config.port:
            merged_cfg["port"] = config.port
        collector = self.register_port(
            port_id,
            merged_cfg,
            baudrate=config.baudrate,
            bytesize=config.bytesize,
            stopbits=config.stopbits,
            direction=config.direction,
            on_frame=on_frame,
            on_raw=on_raw,
            on_error=on_error,
            on_mcu_frame=on_mcu_frame,
            on_connection_error=on_connection_error,
            mcu_cfg=config.mcu_cfg,
        )
        collector.tx_queue_size = config.tx_queue_size
        collector.max_reconnect_attempts = config.max_reconnect_attempts
        collector.reconnect_delay = config.reconnect_delay
        return collector

    def get_port_status(self, port_id: str) -> SerialPortStatus | None:
        """Get a structured status snapshot for a port."""
        collector = self.get_collector(port_id)
        if collector is None:
            return None
        return SerialPortStatus.from_collector(port_id, collector)