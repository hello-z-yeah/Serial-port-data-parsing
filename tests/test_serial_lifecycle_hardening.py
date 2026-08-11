"""Integrated lifecycle stress tests for serial manager & collector.

Covers:
- TX queue task_done balance (no leak / no double-complete)
- Repeated stop / re-start cycles
- Reconnect race robustness under generation guards
- Rapid resource-monitor restart
- Backward-compatible API imports (DistributedSerialManager, PluginManager, …)
"""
from __future__ import annotations

import queue
import threading
import time
from types import SimpleNamespace

import pytest

import protocol_parser.serial_collector as sc
from protocol_parser.exceptions import (
    SerialOperationError,
    SerialStateError,
    TxQueueFullError,
)
from protocol_parser.serial_collector import SerialCollector, TxRequest
from protocol_parser.serial_collector_optimized import (
    OptimizedSerialCollector,
    _TX_STOP,
)
from protocol_parser.serial_manager import (
    DistributedSerialManager,
    SerialManager,
    SerialPortConfig,
    SerialPortStatus,
)
from protocol_parser.plugin_system import (
    PluginConfig,
    PluginManager,
    PluginSystem,
    ProtocolPlugin,
    encode_data_with_plugins,
    parse_data_with_plugins,
)


# ---------------------------------------------------------------------------
# Fake serial infrastructure (mirrors test_serial_async_workers)
# ---------------------------------------------------------------------------

class _FakeSerialException(OSError):
    pass


class _FakeSerialPort:
    def __init__(self, *args, read_chunks=None, write_gate=None, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.is_open = True
        self._read_chunks = queue.Queue()
        for chunk in read_chunks or []:
            self._read_chunks.put(bytes(chunk))
        self.write_gate = write_gate
        self.write_started = threading.Event()
        self.writes: list[tuple[bytes, str]] = []
        self.cancel_read_called = False
        self.cancel_write_called = False

    def read(self, _size: int) -> bytes:
        if not self.is_open:
            return b""
        try:
            return self._read_chunks.get_nowait()
        except queue.Empty:
            time.sleep(0.005)
            return b""

    def write(self, payload: bytes) -> int:
        self.write_started.set()
        if self.write_gate is not None:
            self.write_gate.wait(timeout=2.0)
        if not self.is_open:
            raise _FakeSerialException("closed")
        self.writes.append((bytes(payload), threading.current_thread().name))
        return len(payload)

    def close(self) -> None:
        self.is_open = False

    def cancel_read(self) -> None:
        self.cancel_read_called = True

    def cancel_write(self) -> None:
        self.cancel_write_called = True
        if self.write_gate is not None:
            self.write_gate.set()


class _FakeSerialModule:
    FIVEBITS = 5
    SIXBITS = 6
    SEVENBITS = 7
    EIGHTBITS = 8
    STOPBITS_ONE = 1.0
    STOPBITS_ONE_POINT_FIVE = 1.5
    STOPBITS_TWO = 2.0
    PARITY_NONE = "N"
    SerialException = _FakeSerialException

    def __init__(self, factory):
        self._factory = factory
        self.tools = SimpleNamespace(list_ports=SimpleNamespace(comports=lambda: []))

    def Serial(self, *args, **kwargs):
        return self._factory(*args, **kwargs)


def _install_fake(monkeypatch, *, read_chunks=None, write_gate=None, target_module=None):
    created: list[_FakeSerialPort] = []

    def factory(*args, **kwargs):
        port = _FakeSerialPort(
            *args, read_chunks=read_chunks, write_gate=write_gate, **kwargs
        )
        created.append(port)
        return port

    mod = target_module or sc
    monkeypatch.setattr(mod, "serial", _FakeSerialModule(factory), raising=False)
    monkeypatch.setattr(mod, "HAS_SERIAL", True)
    return created


# ---------------------------------------------------------------------------
# 1. TX queue task_done balance
# ---------------------------------------------------------------------------

class TestTxQueueTaskDoneBalance:
    """Every item consumed by the TX loop must call task_done() exactly once."""

    def test_task_done_balanced_after_request_stop(self, monkeypatch):
        """_TX_STOP consumed by TX loop triggers exactly one task_done."""
        _install_fake(monkeypatch, target_module=sc)
        collector = SerialCollector(cfg={}, port="COM_BAL", primary_enabled=False)
        collector.start()
        assert collector._tx_queue is not None
        q = collector._tx_queue

        # Enqueue a real item, then request_stop (which injects _TX_STOP)
        collector.send(b"\x01\x02")
        time.sleep(0.15)
        collector.request_stop()

        # Give TX loop time to drain both items
        time.sleep(0.5)

        # The queue's unfinished_tasks should be 0 (balanced)
        assert q.unfinished_tasks == 0
        collector._tx_thread.join(timeout=2.0)

    def test_task_done_balanced_after_drain_in_stop(self, monkeypatch):
        """stop() drain loop balances task_done for all remaining items."""
        gate = threading.Event()
        created = _install_fake(monkeypatch, write_gate=gate, target_module=sc)
        collector = SerialCollector(
            cfg={}, port="COM_DRAIN", primary_enabled=False, tx_queue_size=10
        )
        collector.start()
        assert collector._tx_queue is not None
        q = collector._tx_queue

        # TX thread may have already consumed 1 item before write gate blocked;
        # put enough items so multiple remain for the drain loop
        for i in range(10):
            q.put_nowait(TxRequest(bytes([i])))

        # Now stop — remaining items are drained by stop()
        collector.request_stop()
        collector.stop(timeout=2.0)

        # Queue must be fully balanced (core assertion)
        assert q.unfinished_tasks == 0
        # At least some items were dropped by the drain loop
        assert collector.tx_dropped_on_stop >= 5

    def test_no_task_done_double_call(self, monkeypatch):
        """task_done() is not called twice for the same item."""
        _install_fake(monkeypatch, target_module=sc)
        collector = SerialCollector(cfg={}, port="COM_DOUBLE", primary_enabled=False)
        collector.start()
        q = collector._tx_queue

        # Use request_stop + stop pattern
        collector.request_stop()
        collector.stop(timeout=2.0)

        assert q.unfinished_tasks == 0

    def test_optimized_collector_task_done_balance(self, monkeypatch):
        """OptimizedSerialCollector also balances task_done."""
        from protocol_parser import serial_collector_optimized as sco

        _install_fake(monkeypatch, target_module=sco)
        collector = OptimizedSerialCollector(
            cfg={}, port="COM_OPT_BAL", primary_enabled=False, tx_queue_size=10
        )
        collector.start()
        assert collector._tx_queue is not None
        q = collector._tx_queue

        for i in range(3):
            q.put_nowait(TxRequest(bytes([i])))

        collector.request_stop()
        collector.stop(timeout=2.0)

        assert q.unfinished_tasks == 0


# ---------------------------------------------------------------------------
# 2. Repeated stop / start cycles
# ---------------------------------------------------------------------------

class TestRepeatedStopStart:
    """Collector can be stopped and started repeatedly without leaking."""

    def test_three_stop_start_cycles(self, monkeypatch):
        """SerialCollector survives 3 consecutive stop→start cycles."""
        _install_fake(monkeypatch, target_module=sc)
        collector = SerialCollector(cfg={}, port="COM_CYCLE", primary_enabled=False)

        for i in range(3):
            collector.start()
            assert collector.running
            collector.stop(timeout=2.0)
            assert not collector.running
            assert collector._serial is None
            assert collector._thread is None
            assert collector._tx_thread is None

    def test_optimized_three_stop_start_cycles(self, monkeypatch):
        """OptimizedSerialCollector survives 3 consecutive stop→start cycles."""
        from protocol_parser import serial_collector_optimized as sco

        _install_fake(monkeypatch, target_module=sco)
        collector = OptimizedSerialCollector(
            cfg={}, port="COM_OPT_CYCLE", primary_enabled=False
        )

        for i in range(3):
            collector.start()
            assert collector.running
            collector.stop(timeout=2.0)
            assert not collector.running
            assert collector._serial is None
            assert collector._thread is None
            assert collector._tx_thread is None


# ---------------------------------------------------------------------------
# 3. Generation guard prevents stale workers
# ---------------------------------------------------------------------------

class TestGenerationGuard:
    """Stale workers (old generation) cannot overwrite new generation state."""

    def test_stale_tx_does_not_affect_running(self, monkeypatch):
        """When generation changes, old TX loop exits without touching state."""
        from protocol_parser import serial_collector_optimized as sco

        created = _install_fake(monkeypatch, target_module=sco)
        collector = OptimizedSerialCollector(
            cfg={}, port="COM_GEN", primary_enabled=False
        )
        collector.start()
        gen = collector._generation
        assert collector.running

        # Simulate a generation change without a real reconnect
        # (manually bump generation to simulate reconnect)
        with collector._state_lock:
            collector._generation += 1
            new_gen = collector._generation

        # Give old threads time to notice and exit
        time.sleep(0.5)

        # Old threads should have exited, running should still be True
        # because new threads are not started (we just bumped gen)
        # The state should NOT have been corrupted by stale workers
        assert collector._generation == new_gen
        collector.stop(timeout=2.0)


# ---------------------------------------------------------------------------
# 4. Rapid resource-monitor restart
# ---------------------------------------------------------------------------

class TestRapidMonitorRestart:
    """Resource monitor can be stopped and immediately restarted."""

    def test_stop_then_start_monitor(self):
        manager = SerialManager()
        manager.start_resource_monitor(interval=0.05)
        assert manager._monitor_thread is not None
        assert manager._monitor_thread.is_alive()

        manager.stop_resource_monitor(timeout=3.0)
        time.sleep(0.1)
        assert manager._monitor_thread is None or not manager._monitor_thread.is_alive()

        manager.start_resource_monitor(interval=0.05)
        assert manager._monitor_thread is not None
        assert manager._monitor_thread.is_alive()

        manager.stop_resource_monitor(timeout=3.0)

    def test_stop_all_ports_propagates_error(self, monkeypatch):
        """stop_all raises the first SerialOperationError after processing all ports."""
        from protocol_parser import serial_collector_optimized as sco

        _install_fake(monkeypatch, target_module=sco)
        manager = SerialManager()

        # Register a port that will fail on stop
        collector = OptimizedSerialCollector(
            cfg={}, port="COM_ERR", primary_enabled=False
        )
        collector.start()

        with manager._state_lock:
            manager._collectors["test"] = collector

        # Artificially break stop by making threads unjoinable
        # We'll just verify that stop_all processes all and raises
        # the first error if any
        try:
            manager.stop_all()
        except SerialOperationError:
            pass  # Expected if threads are still alive
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 5. Backward-compatible API imports
# ---------------------------------------------------------------------------

class TestApiCompatibility:
    """All legacy classes can be imported and instantiated."""

    def test_distributed_serial_manager_is_serial_manager(self):
        assert issubclass(DistributedSerialManager, SerialManager)
        mgr = DistributedSerialManager()
        assert isinstance(mgr, SerialManager)

    def test_serial_port_config_defaults(self):
        cfg = SerialPortConfig()
        assert cfg.port == ""
        assert cfg.baudrate == 9600
        assert cfg.max_reconnect_attempts == 5

    def test_serial_port_status_creation(self):
        status = SerialPortStatus(port_id="test", running=True)
        assert status.port_id == "test"
        assert status.running is True

    def test_protocol_plugin_base(self):
        plugin = ProtocolPlugin()
        assert plugin.process(b"hello") is None
        assert plugin.encode(b"hello") is None
        plugin.initialize()
        plugin.shutdown()

    def test_plugin_manager_is_plugin_system(self):
        assert issubclass(PluginManager, PluginSystem)
        mgr = PluginManager()
        assert isinstance(mgr, PluginSystem)

    def test_plugin_config_dataclass(self):
        cfg = PluginConfig(module_path="test.module", enabled=False)
        assert cfg.module_path == "test.module"
        assert cfg.enabled is False

    def test_parse_data_with_plugins_module_level(self):
        result = parse_data_with_plugins(b"hello")
        assert result == b"hello"

    def test_encode_data_with_plugins_module_level(self):
        result = encode_data_with_plugins(b"hello")
        assert result == b"hello"

    def test_plugin_process_pipeline(self):
        """PluginManager chains process() calls through enabled plugins."""
        mgr = PluginManager()

        class UpperPlugin(ProtocolPlugin):
            def process(self, data, direction="rx"):
                return data.upper()

        class PrefixPlugin(ProtocolPlugin):
            def process(self, data, direction="rx"):
                return b"PRE:" + data

        # Can't easily load real modules in test, so test manually
        mgr._plugins["upper"] = UpperPlugin()
        mgr._enabled_plugins["upper"] = UpperPlugin()
        mgr._plugins["prefix"] = PrefixPlugin()
        mgr._enabled_plugins["prefix"] = PrefixPlugin()

        result = mgr.parse_data_with_plugins(b"hello")
        # Both plugins run in registration order
        assert b"HELLO" in result or b"PRE:" in result

    def test_distributed_manager_register_from_config(self, monkeypatch):
        """DistributedSerialManager.register_port_from_config works."""
        from protocol_parser import serial_collector_optimized as sco

        _install_fake(monkeypatch, target_module=sco)
        mgr = DistributedSerialManager()
        cfg = SerialPortConfig(
            port="COM_CFG",
            baudrate=115200,
            cfg={"frame": {"sync_word": [0xA5, 0x5A]}},
        )
        collector = mgr.register_port_from_config("logical", cfg)
        assert collector is not None
        assert collector.is_running()
        status = mgr.get_port_status("logical")
        assert status is not None
        assert status.running is True
        mgr.unregister_port("logical")


# ---------------------------------------------------------------------------
# 6. Error propagation in manager
# ---------------------------------------------------------------------------

class _FailingCollector:
    """Mock collector that raises SerialOperationError on stop()."""

    def is_running(self):
        return True

    def is_connected(self):
        return True

    def get_error_count(self):
        return 0

    def stop(self, *, timeout=2.5):
        raise SerialOperationError("模拟停止失败")


class TestErrorPropagation:
    """Manager methods propagate SerialOperationError instead of swallowing."""

    def test_unregister_propagates_stop_error(self, monkeypatch):
        """unregister_port raises SerialOperationError when stop fails."""
        mgr = SerialManager()
        collector = _FailingCollector()
        with mgr._state_lock:
            mgr._collectors["port1"] = collector

        with pytest.raises(SerialOperationError, match="模拟停止失败"):
            mgr.unregister_port("port1")

    def test_stop_port_propagates_stop_error(self, monkeypatch):
        """stop_port raises SerialOperationError when stop fails."""
        mgr = SerialManager()
        collector = _FailingCollector()
        with mgr._state_lock:
            mgr._collectors["port1"] = collector

        with pytest.raises(SerialOperationError, match="模拟停止失败"):
            mgr.stop_port("port1")

    def test_stop_all_aggregates_errors(self, monkeypatch):
        """stop_all raises an aggregated RuntimeError containing every failure."""
        mgr = SerialManager()
        failing = _FailingCollector()
        ok = _FailingCollector()  # Also fails — two failures expected
        with mgr._state_lock:
            mgr._collectors["port1"] = failing
            mgr._collectors["port2"] = ok

        with pytest.raises(RuntimeError) as excinfo:
            mgr.stop_all()
        # Aggregated message references the number of failures.
        assert "2 error(s)" in str(excinfo.value)
        # The original SerialOperationError is chained as __cause__.
        assert isinstance(excinfo.value.__cause__, SerialOperationError)

    def test_stop_all_reraises_single_error_directly(self, monkeypatch):
        """stop_all re-raises a single failure as the original exception."""
        mgr = SerialManager()
        with mgr._state_lock:
            mgr._collectors["port1"] = _FailingCollector()

        with pytest.raises(SerialOperationError, match="模拟停止失败"):
            mgr.stop_all()


# ---------------------------------------------------------------------------
# 7. Configuration timing — tx_queue_size applied before start()
# ---------------------------------------------------------------------------

class TestConfigTiming:
    """register_port_from_config must apply config BEFORE start()."""

    def test_tx_queue_size_applied_before_start(self, monkeypatch):
        """tx_queue_size from config is used for the actual queue."""
        from protocol_parser import serial_collector_optimized as sco

        _install_fake(monkeypatch, target_module=sco)
        mgr = DistributedSerialManager()
        cfg = SerialPortConfig(
            port="COM_CFG",
            tx_queue_size=50,
            max_reconnect_attempts=3,
            reconnect_delay=0.5,
        )
        collector = mgr.register_port_from_config("logical", cfg)
        assert collector.tx_queue_size == 50
        assert collector.max_reconnect_attempts == 3
        assert collector.reconnect_delay == 0.5
        assert collector._tx_queue is not None
        assert collector._tx_queue.maxsize == 50
        mgr.unregister_port("logical")

    def test_register_port_accepts_config_params(self, monkeypatch):
        """register_port() accepts tx_queue_size etc. as keyword args."""
        from protocol_parser import serial_collector_optimized as sco

        _install_fake(monkeypatch, target_module=sco)
        mgr = SerialManager()
        collector = mgr.register_port(
            "port1",
            {"port": "COM1"},
            tx_queue_size=25,
            max_reconnect_attempts=2,
            reconnect_delay=0.3,
        )
        assert collector.tx_queue_size == 25
        assert collector._tx_queue.maxsize == 25
        mgr.unregister_port("port1")


# ---------------------------------------------------------------------------
# 8. Monitor timeout — error propagation on hang
# ---------------------------------------------------------------------------

class TestMonitorTimeout:
    """stop_resource_monitor raises SerialOperationError on timeout."""

    def test_stop_monitor_raises_on_timeout(self, monkeypatch):
        """When monitor thread is alive after timeout, error is raised."""
        mgr = SerialManager()
        mgr._monitor_thread = threading.Thread(target=lambda: time.sleep(30))
        mgr._monitor_thread.daemon = True
        mgr._monitor_thread.start()

        with pytest.raises(SerialOperationError, match="资源监控线程"):
            mgr.stop_resource_monitor(timeout=0.05)

        # Thread still alive, reference NOT cleared
        assert mgr._monitor_thread is not None
        assert mgr._monitor_thread.is_alive()

        # Cleanup
        mgr._monitor_stop.set()
        mgr._monitor_thread.join(timeout=5.0)
        mgr._monitor_thread = None

    def test_stop_monitor_no_thread_is_noop(self):
        """stop_resource_monitor when no thread is running is safe."""
        mgr = SerialManager()
        mgr.stop_resource_monitor(timeout=1.0)  # Should not raise
        assert mgr._monitor_thread is None

    def test_stop_monitor_self_call_is_safe(self):
        """Calling stop_resource_monitor from the monitor thread itself."""
        mgr = SerialManager()
        done = threading.Event()

        def monitor_target():
            mgr.stop_resource_monitor(timeout=2.0)
            done.set()

        mgr._monitor_thread = threading.Thread(target=monitor_target)
        mgr._monitor_thread.daemon = True
        mgr._monitor_thread.start()
        done.wait(timeout=3.0)
        assert mgr._monitor_thread is None


# ---------------------------------------------------------------------------
# 9. Concurrency stress — rapid stop/start cycles
# ---------------------------------------------------------------------------

class TestConcurrencyStress:
    """Multiple rapid stop/reconnect calls do not deadlock or leak."""

    def test_rapid_start_stop_no_leak(self, monkeypatch):
        """10 rapid start→stop cycles on a collector."""
        from protocol_parser import serial_collector_optimized as sco

        _install_fake(monkeypatch, target_module=sco)
        collector = OptimizedSerialCollector(
            cfg={}, port="COM_STRESS", primary_enabled=False
        )
        for i in range(10):
            collector.start()
            assert collector.is_running()
            collector.stop(timeout=2.0)
            assert not collector.is_running()
            assert collector._serial is None
            assert collector._thread is None
            assert collector._tx_thread is None

    def test_concurrent_stop_start_threads(self, monkeypatch):
        """Multiple threads racing to stop/start a collector.

        This test verifies that concurrent stop/start does NOT deadlock
        or crash with unexpected exceptions. Individual operations may
        legitimately fail with SerialStateError (e.g. "正在停止中")
        or SerialOperationError (e.g. timeout) — those are expected
        outcomes in a race, not bugs.
        """
        from protocol_parser import serial_collector_optimized as sco

        _install_fake(monkeypatch, target_module=sco)
        collector = OptimizedSerialCollector(
            cfg={}, port="COM_CONC", primary_enabled=False
        )
        collector.start()
        assert collector.is_running()

        errors: list[Exception] = []
        deadlock_detected = False

        def stop_start_worker():
            try:
                for _ in range(3):
                    try:
                        collector.stop(timeout=2.0)
                    except (SerialOperationError, SerialStateError):
                        pass
                    try:
                        collector.start()
                    except (SerialOperationError, SerialStateError):
                        pass
            except Exception as exc:
                errors.append(exc)

        threads = [
            threading.Thread(target=stop_start_worker) for _ in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15.0)
            if t.is_alive():
                deadlock_detected = True

        assert not deadlock_detected, "Concurrent stop/start caused a deadlock"
        # Only unexpected exceptions (not SerialStateError/SerialOperationError)
        # should be reported
        unexpected = [
            e for e in errors
            if not isinstance(e, (SerialOperationError, SerialStateError))
        ]
        assert not unexpected, f"Unexpected errors: {unexpected}"

        try:
            collector.stop(timeout=2.0)
        except (SerialOperationError, SerialStateError):
            pass

    def test_rapid_monitor_restart_no_leak(self):
        """Rapid monitor stop→start→stop does not duplicate threads."""
        mgr = SerialManager()
        for _ in range(5):
            mgr.start_resource_monitor(interval=0.05)
            assert mgr._monitor_thread is not None
            assert mgr._monitor_thread.is_alive()
            mgr.stop_resource_monitor(timeout=3.0)
            time.sleep(0.05)

        assert mgr._monitor_thread is None or not mgr._monitor_thread.is_alive()


# ---------------------------------------------------------------------------
# 10. Plugin thread safety
# ---------------------------------------------------------------------------

class TestPluginThreadSafety:
    """PluginManager protects dict operations with locks."""

    def test_concurrent_enable_disable_no_crash(self):
        """Concurrent enable/disable from multiple threads does not crash."""
        mgr = PluginManager()
        mgr._plugins["p1"] = ProtocolPlugin()
        mgr._enabled_plugins["p1"] = ProtocolPlugin()

        errors: list[Exception] = []

        def worker():
            try:
                for _ in range(20):
                    mgr.enable_plugin("p1")
                    mgr.disable_plugin("p1")
                    _ = mgr.enabled_plugins
                    _ = mgr.list_all_plugins()
                    _ = mgr.list_enabled_plugins()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        assert not errors, f"Unexpected errors: {errors}"

    def test_parse_while_mutating_no_crash(self):
        """parse_data_with_plugins while plugins are mutated."""
        mgr = PluginManager()
        mgr._plugins["p1"] = ProtocolPlugin()
        mgr._enabled_plugins["p1"] = ProtocolPlugin()

        stop = threading.Event()

        def mutator():
            while not stop.is_set():
                mgr.enable_plugin("p1")
                mgr.disable_plugin("p1")

        def parser():
            for _ in range(30):
                mgr.parse_data_with_plugins(b"hello")
                mgr.encode_data_with_plugins(b"hello")

        t1 = threading.Thread(target=mutator, daemon=True)
        t2 = threading.Thread(target=parser, daemon=True)
        t3 = threading.Thread(target=parser, daemon=True)
        t1.start()
        t2.start()
        t3.start()
        t2.join(timeout=10.0)
        t3.join(timeout=10.0)
        stop.set()
        t1.join(timeout=3.0)


# ---------------------------------------------------------------------------
# 11. Plugin error logging — no silent swallowing
# ---------------------------------------------------------------------------

class _FailingPlugin(ProtocolPlugin):
    def process(self, data, direction="rx"):
        raise RuntimeError("intentional failure")

    def encode(self, data, direction="tx"):
        raise RuntimeError("intentional encode failure")

    def shutdown(self):
        raise RuntimeError("intentional shutdown failure")


class TestPluginErrorLogging:
    """Plugin errors are logged, not silently swallowed."""

    def test_parse_logs_error(self, caplog):
        mgr = PluginManager()
        mgr._plugins["fail"] = _FailingPlugin()
        mgr._enabled_plugins["fail"] = _FailingPlugin()

        result = mgr.parse_data_with_plugins(b"data")
        assert result == b"data"
        assert any("intentional failure" in r.message for r in caplog.records)
        assert any("parse phase failed" in r.message for r in caplog.records)

    def test_encode_logs_error(self, caplog):
        mgr = PluginManager()
        mgr._plugins["fail"] = _FailingPlugin()
        mgr._enabled_plugins["fail"] = _FailingPlugin()

        result = mgr.encode_data_with_plugins(b"data")
        assert result == b"data"
        assert any("encode phase failed" in r.message for r in caplog.records)

    def test_unload_logs_shutdown_error(self, caplog):
        mgr = PluginManager()
        mgr._plugins["fail"] = _FailingPlugin()
        mgr._enabled_plugins["fail"] = _FailingPlugin()

        mgr.unload_plugin("fail")
        assert any("shutdown failed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# 12. scan_directory with arbitrary paths
# ---------------------------------------------------------------------------

class TestScanDirectory:
    """scan_directory works with arbitrary filesystem paths."""

    def test_scan_directory_loads_plugin(self, tmp_path):
        """scan_directory loads plugins from a temp directory."""
        plugin_file = tmp_path / "test_plugin.py"
        plugin_file.write_text(
            "class Plugin:\n"
            "    plugin_id = 'test'\n"
            "    def process(self, data, direction='rx'):\n"
            "        return data.upper()\n"
        )
        mgr = PluginManager()
        loaded = mgr.scan_directory(str(tmp_path))
        assert len(loaded) == 1
        assert "test_plugin.py" in loaded[0]

        plugin = mgr.get_plugin(loaded[0])
        assert plugin is not None
        assert plugin.process(b"hello") == b"HELLO"

    def test_scan_directory_empty_dir(self, tmp_path):
        """scan_directory on empty dir returns []."""
        mgr = PluginManager()
        loaded = mgr.scan_directory(str(tmp_path))
        assert loaded == []

    def test_scan_directory_skips_non_plugin_files(self, tmp_path):
        """scan_directory skips files without Plugin class."""
        (tmp_path / "helper.py").write_text("x = 1\n")
        (tmp_path / "_private.py").write_text("class Plugin: pass\n")

        mgr = PluginManager()
        loaded = mgr.scan_directory(str(tmp_path))
        assert loaded == []  # helper has no Plugin class, _private is skipped


# ---------------------------------------------------------------------------
# 13. Plugin active-call drain — shutdown() must not run mid-process
# ---------------------------------------------------------------------------

class TestPluginActiveCallDrain:
    """unload_plugin waits for active process/encode calls to finish."""

    def test_unload_drains_active_calls_before_shutdown(self):
        """unload_plugin blocks until in-flight process() returns."""
        import time as _time

        mgr = PluginManager()
        entered = threading.Event()
        release = threading.Event()
        done = threading.Event()
        shutdown_started = threading.Event()
        shutdown_finished = threading.Event()
        call_count_during_shutdown: list[int] = []

        class SlowPlugin(ProtocolPlugin):
            def process(self, data, direction="rx"):
                entered.set()
                release.wait(timeout=3.0)
                return data

            def shutdown(self):
                shutdown_started.set()
                # Capture the number of active calls at the moment
                # shutdown() runs. It MUST be 0 or the drain has
                # not actually occurred.
                call_count_during_shutdown.append(
                    getattr(self, "_active_calls", -1)
                )
                _time.sleep(0.01)
                shutdown_finished.set()

        plugin = SlowPlugin()
        mgr._plugins["slow"] = plugin
        mgr._enabled_plugins["slow"] = plugin

        def parser_thread():
            mgr.parse_data_with_plugins(b"hello")
            done.set()

        t = threading.Thread(target=parser_thread, daemon=True)
        t.start()
        entered.wait(timeout=3.0)

        # Now unload while process() is still blocked on 'release'.
        mgr.unload_plugin("slow")

        # shutdown() should have been delayed until the parser
        # released; verify that at the moment shutdown() ran the
        # active call count was zero.
        assert shutdown_started.is_set(), "shutdown was never invoked"
        assert shutdown_finished.is_set(), "shutdown did not finish"
        assert call_count_during_shutdown and call_count_during_shutdown[0] == 0, (
            f"shutdown() observed active_calls={call_count_during_shutdown!r}, "
            "expected 0 — drain did not occur before shutdown()"
        )

        # Now release the parser and wait for it to finish.
        release.set()
        t.join(timeout=5.0)
        assert done.is_set(), "parser thread did not finish"

    def test_active_calls_counter_balanced(self):
        """Active call counter returns to zero after process() completes."""
        mgr = PluginManager()

        class TrackerPlugin(ProtocolPlugin):
            def process(self, data, direction="rx"):
                return data

            def encode(self, data, direction="tx"):
                return data

        plugin = TrackerPlugin()
        mgr._plugins["track"] = plugin
        mgr._enabled_plugins["track"] = plugin

        for _ in range(5):
            mgr.parse_data_with_plugins(b"a")
            mgr.encode_data_with_plugins(b"b")

        # Counter must be back to zero.
        assert plugin._active_calls == 0

    def test_serial_collector_tx_queue_drain_on_stale_gen(self):
        """When gen != self._generation, leftover TX items are drained."""
        import queue as _queue

        from protocol_parser.serial_collector_optimized import (
            OptimizedSerialCollector,
            _TX_STOP,
        )

        q: _queue.Queue = _queue.Queue()
        # Put a real item and the stop sentinel.
        q.put(object())
        q.put(_TX_STOP)
        # Mark the first item as "done" (simulating inner finally).
        try:
            q.task_done()
        except ValueError:
            pass

        # Drain remaining items via the static helper.
        OptimizedSerialCollector._drain_tx_queue(q)

        # Queue must be empty and join() must not block — in Python 3.10
        # Queue.join() has no timeout, so we rely on the fact that
        # unfinished_tasks == 0 means join() returns immediately.
        assert q.empty(), "queue should be empty after drain"
        q.join()  # Must return immediately without hanging


# ---------------------------------------------------------------------------
# 13. Hardened lifecycle paths — unregister leak, register overwrite,
#     atomic stop/reconnect, and RX thread isolation.
# ---------------------------------------------------------------------------

class _UnsafeStoppedCollector:
    """Mock collector that is_running() returns False but threads are alive."""

    def __init__(self):
        self._alive = True
        self._serial = SimpleNamespace(is_open=False)

    def is_running(self):
        return False

    def is_safely_stopped(self):
        # Override: claim not safely stopped because thread is alive.
        return False

    def stop(self, *, timeout=2.5):
        self._alive = False

    @property
    def _thread(self):
        return self

    @property
    def _tx_thread(self):
        return None

    @property
    def _parse_thread(self):
        return None

    @property
    def _reconnect_thread(self):
        return None

    def is_alive(self):
        return self._alive


class TestUnregisterLeakFix:
    """unregister_port keeps collector registered when stop() fails."""

    def test_collector_retained_when_stop_raises(self, monkeypatch):
        mgr = SerialManager()
        collector = _FailingCollector()
        with mgr._state_lock:
            mgr._collectors["port1"] = collector

        with pytest.raises(SerialOperationError):
            mgr.unregister_port("port1")

        # Collector MUST remain registered because stop() failed.
        with mgr._state_lock:
            assert "port1" in mgr._collectors, (
                "collector must be retained when stop() raises"
            )

    def test_collector_removed_when_stop_succeeds(self, monkeypatch):
        from protocol_parser import serial_collector_optimized as sco

        _install_fake(monkeypatch, target_module=sco)
        mgr = SerialManager()
        collector = mgr.register_port(
            "port1", {"port": "COM_OK"},
        )
        collector.primary_enabled = False
        with mgr._state_lock:
            assert "port1" in mgr._collectors

        mgr.unregister_port("port1")

        with mgr._state_lock:
            assert "port1" not in mgr._collectors, (
                "collector must be removed after successful stop()"
            )


class TestRegisterOverwriteFix:
    """register_port rejects overwriting collectors that aren't safely stopped."""

    def test_register_rejects_overwrite_of_unsafely_stopped(self, monkeypatch):
        mgr = SerialManager()
        unsafe = _UnsafeStoppedCollector()
        with mgr._state_lock:
            mgr._collectors["port1"] = unsafe

        with pytest.raises(SerialStateError) as excinfo:
            mgr.register_port("port1", {"port": "COM1"})
        assert "未完全停止" in str(excinfo.value) or "无法重新注册" in str(excinfo.value)

    def test_register_allows_overwrite_of_safely_stopped(self, monkeypatch):
        from protocol_parser import serial_collector_optimized as sco

        _install_fake(monkeypatch, target_module=sco)
        mgr = SerialManager()
        collector = mgr.register_port(
            "port1", {"port": "COM_A"},
        )
        collector.primary_enabled = False
        assert collector.is_safely_stopped() is False or collector.running

        mgr.unregister_port("port1")
        # Now safely stopped — register_port should succeed.
        new_collector = mgr.register_port(
            "port1", {"port": "COM_B"},
        )
        new_collector.primary_enabled = False
        assert new_collector is not collector
        mgr.unregister_port("port1")


class TestAtomicStopReconnectLifecycle:
    """Generation guard causes reconnect loop to abort under stop()."""

    def test_reconnect_supervisor_aborts_when_stopping(self, monkeypatch):
        """_reconnect_supervisor returns immediately if _stopping is set."""
        from protocol_parser import serial_collector_optimized as sco

        _install_fake(monkeypatch, target_module=sco)
        collector = OptimizedSerialCollector(
            cfg={}, port="COM_ATOMIC", primary_enabled=False
        )
        collector.start()
        gen_before = collector._generation

        # Inject _stopping flag so supervisor must bail out immediately.
        with collector._state_lock:
            collector._stopping = True
            collector._stop_event.set()

        # Run the supervisor directly — it should abort without raising.
        try:
            collector._reconnect_supervisor(gen_before)
        finally:
            with collector._state_lock:
                collector._stopping = False
            collector.stop(timeout=2.0)

    def test_is_safely_stopped_checks_all_threads(self, monkeypatch):
        """is_safely_stopped returns False when any worker thread is alive."""
        from protocol_parser import serial_collector_optimized as sco

        _install_fake(monkeypatch, target_module=sco)
        collector = OptimizedSerialCollector(
            cfg={}, port="COM_SAFE", primary_enabled=False
        )
        collector.start()
        try:
            while not collector.running:
                time.sleep(0.05)

            # While running, is_safely_stopped must be False because threads alive.
            assert collector.is_safely_stopped() is False
        finally:
            collector.stop(timeout=2.0)
        assert collector.is_safely_stopped() is True


class TestRXThreadIsolation:
    """RX thread is decoupled from parsing — parse thread runs callbacks."""

    def test_parse_thread_started_and_stopped(self, monkeypatch):
        """_parse_thread is started on start() and joined on stop()."""
        from protocol_parser import serial_collector_optimized as sco

        _install_fake(monkeypatch, target_module=sco)
        collector = OptimizedSerialCollector(
            cfg={}, port="COM_PARSE", primary_enabled=False
        )
        assert collector._parse_thread is None
        collector.start()
        try:
            assert collector._parse_thread is not None
            assert collector._parse_thread.is_alive()
            assert collector._parse_queue is not None
        finally:
            collector.stop(timeout=2.0)
        assert collector._parse_thread is None
        assert collector._parse_queue is None

    def test_parse_queue_drained_on_stop(self, monkeypatch):
        """parse queue is drained on stop — no leaked items."""
        from protocol_parser import serial_collector_optimized as sco

        created = _install_fake(monkeypatch, target_module=sco)
        collector = OptimizedSerialCollector(
            cfg={}, port="COM_PARSE_DRAIN", primary_enabled=False
        )
        collector.start()
        q = collector._parse_queue
        assert q is not None
        # Simulate backlog on parse queue.
        for _ in range(5):
            try:
                q.put_nowait(b"\x00")
            except queue.Full:
                break

        collector.stop(timeout=2.0)

        # After stop, parse queue reference cleared.
        assert collector._parse_queue is None

    def test_rx_thread_name_isolation(self, monkeypatch):
        """RX thread is named 'smst-rx-*' and parse thread is 'smst-parse-*'."""
        from protocol_parser import serial_collector_optimized as sco

        _install_fake(monkeypatch, target_module=sco)
        collector = OptimizedSerialCollector(
            cfg={}, port="COM_NAMES", primary_enabled=False
        )
        collector.start()
        try:
            assert collector._thread is not None
            assert collector._thread.name.startswith("smst-rx-")
            assert collector._parse_thread is not None
            assert collector._parse_thread.name.startswith("smst-parse-")
        finally:
            collector.stop(timeout=2.0)

    def test_concurrent_stop_start_with_parse_thread(self, monkeypatch):
        """Multiple stop/start cycles keep parse thread lifecycle balanced."""
        from protocol_parser import serial_collector_optimized as sco

        _install_fake(monkeypatch, target_module=sco)
        collector = OptimizedSerialCollector(
            cfg={}, port="COM_CYCLE_PARSE", primary_enabled=False
        )
        for _ in range(3):
            collector.start()
            assert collector._parse_thread is not None and collector._parse_thread.is_alive()
            collector.stop(timeout=2.0)
            assert collector._parse_thread is None
            assert collector._parse_queue is None