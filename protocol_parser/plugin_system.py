"""Plugin system with safe loading, configuration, and caching.

Features:
- Prevents loading disabled plugins into enabled_plugins
- Applies configuration to plugin instance before initialization
- Uses threading.RLock to protect _cache operations
"""
from __future__ import annotations

import importlib
import importlib.util
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

_logger = logging.getLogger(__name__)


class PluginSystem:
    """Plugin loader and manager with thread-safe caching."""

    def __init__(self):
        self._plugins: dict[str, Any] = {}
        self._enabled_plugins: dict[str, Any] = {}
        self._cache: dict[str, Any] = {}
        self._cache_lock = threading.RLock()
        self._plugins_lock = threading.RLock()

    @property
    def enabled_plugins(self) -> dict[str, Any]:
        """Return a shallow-copy snapshot of enabled plugins.

        Returns a copy so that concurrent iteration (e.g. during
        parse/encode) does not raise ``RuntimeError: dictionary
        changed size during iteration``.
        """
        with self._plugins_lock:
            return dict(self._enabled_plugins)

    def load_plugin(
        self,
        module_path: str,
        *,
        plugin_class_name: str = "Plugin",
        enabled: bool = True,
        config: dict | None = None,
    ) -> Any:
        """Load and register a plugin.

        - If enabled is False, the plugin is loaded but NOT added to
          self.enabled_plugins (prevents disabled plugins from appearing
          in the enabled collection).
        - Configuration is applied to the instance BEFORE initialization.
        - ``_plugins_lock`` protects the registration dicts; actual
          import/instantiation happens *outside* the lock to avoid
          holding it during potentially slow I/O.
        """
        module = importlib.import_module(module_path)
        plugin_class = getattr(module, plugin_class_name)
        instance = plugin_class()

        if config is not None:
            self._apply_config(instance, config)

        if hasattr(instance, "initialize"):
            instance.initialize()

        plugin_id = module_path
        with self._plugins_lock:
            self._plugins[plugin_id] = instance
            if enabled:
                self._enabled_plugins[plugin_id] = instance

        return instance

    def _apply_config(self, instance: Any, config: dict) -> None:
        """Apply configuration dict to plugin instance attributes."""
        for key, value in config.items():
            if hasattr(instance, key):
                try:
                    setattr(instance, key, value)
                except (AttributeError, TypeError):
                    pass

    def get_plugin(self, plugin_id: str) -> Any | None:
        """Get a plugin by ID (module path). Thread-safe."""
        with self._plugins_lock:
            return self._plugins.get(plugin_id)

    def enable_plugin(self, plugin_id: str) -> None:
        """Move a plugin from disabled to enabled collection. Thread-safe."""
        with self._plugins_lock:
            plugin = self._plugins.get(plugin_id)
            if plugin is not None:
                self._enabled_plugins[plugin_id] = plugin

    def disable_plugin(self, plugin_id: str) -> None:
        """Move a plugin from enabled to disabled collection. Thread-safe."""
        with self._plugins_lock:
            self._enabled_plugins.pop(plugin_id, None)

    def unload_plugin(self, plugin_id: str) -> None:
        """Unload and remove a plugin. Thread-safe with active-call drain.

        Removes the plugin from the enabled/registered collections
        first so new pipeline iterations can no longer observe it,
        then waits for any in-flight ``process`` / ``encode`` calls
        to finish before invoking ``shutdown()``.
        """
        with self._plugins_lock:
            self._enabled_plugins.pop(plugin_id, None)
            plugin = self._plugins.pop(plugin_id, None)

        if plugin is None:
            return

        # Wait for active calls to drain before shutting the plugin down.
        cond = getattr(plugin, "_active_calls_cond", None)
        if cond is not None:
            with cond:
                while getattr(plugin, "_active_calls", 0) > 0:
                    cond.wait(timeout=0.2)

        if hasattr(plugin, "shutdown"):
            try:
                plugin.shutdown()
            except Exception as exc:
                _logger.exception(
                    "Plugin %s shutdown failed: %s", plugin_id, exc
                )

    def get_cached(self, key: str) -> Any:
        """Retrieve a cached value (thread-safe)."""
        with self._cache_lock:
            return self._cache.get(key)

    def set_cached(self, key: str, value: Any) -> None:
        """Store a value in the cache (thread-safe)."""
        with self._cache_lock:
            self._cache[key] = value

    def delete_cached(self, key: str) -> None:
        """Remove a cached value (thread-safe)."""
        with self._cache_lock:
            self._cache.pop(key, None)

    def clear_cache(self) -> None:
        """Clear all cached values (thread-safe)."""
        with self._cache_lock:
            self._cache.clear()

    def compute_cached(self, key: str, loader: Callable[[], Any]) -> Any:
        """Get or compute and cache a value (thread-safe with RLock).

        Uses RLock to allow reentrant calls from within the loader function
        without deadlocking.
        """
        with self._cache_lock:
            if key in self._cache:
                return self._cache[key]
        value = loader()
        with self._cache_lock:
            self._cache[key] = value
        return value

    def list_all_plugins(self) -> dict[str, Any]:
        """Return all registered plugins (both enabled and disabled). Thread-safe."""
        with self._plugins_lock:
            return dict(self._plugins)

    def list_enabled_plugins(self) -> dict[str, Any]:
        """Return only enabled plugins. Thread-safe."""
        with self._plugins_lock:
            return dict(self._enabled_plugins)


# ---------------------------------------------------------------------------
# Backward-compatibility layer for legacy plugin API
# ---------------------------------------------------------------------------

@dataclass
class PluginConfig:
    """Serializable configuration for a plugin."""

    module_path: str = ""
    plugin_class_name: str = "Plugin"
    enabled: bool = True
    config: dict = field(default_factory=dict)


class ProtocolPlugin:
    """Base class for protocol-aware plugins.

    Subclasses override ``process`` and/or ``encode`` to intercept
    raw data flowing through the serial pipeline.

    Active call tracking: each call to ``process`` / ``encode`` bumps
    ``_active_calls``; ``unload_plugin`` waits for the counter to
    reach zero before invoking ``shutdown`` so that a plugin is never
    shut down while a pipeline thread is still executing its hook.
    """

    plugin_id: str = ""
    enabled: bool = True

    def __init__(self) -> None:
        self._active_calls: int = 0
        self._active_calls_lock = threading.Lock()
        self._active_calls_cond = threading.Condition(self._active_calls_lock)

    def initialize(self) -> None:
        """Called once after loading. Override to set up resources."""

    def shutdown(self) -> None:
        """Called before unloading. Override to release resources."""

    def _enter_active(self) -> None:
        with self._active_calls_cond:
            self._active_calls += 1

    def _leave_active(self) -> None:
        with self._active_calls_cond:
            self._active_calls -= 1
            if self._active_calls <= 0:
                self._active_calls_cond.notify_all()

    def process(self, data: bytes, direction: str = "rx") -> bytes | None:
        """Process raw data. Return modified bytes or None to pass through."""
        return None

    def encode(self, data: bytes, direction: str = "tx") -> bytes | None:
        """Encode raw data. Return modified bytes or None to pass through."""
        return None


class PluginManager(PluginSystem):
    """Extended plugin manager with protocol-aware processing.

    Provides ``parse_data_with_plugins`` and ``encode_data_with_plugins``
    as top-level entry points that iterate over enabled plugins.
    """

    def parse_data_with_plugins(
        self, data: bytes, *, direction: str = "rx"
    ) -> bytes:
        """Run all enabled plugins' ``process`` on *data* in sequence.

        Each plugin receives the output of the previous one. If a plugin
        returns ``None`` the input is passed through unchanged.
        Exceptions from individual plugins are logged with full
        tracebacks so that faulty plugins can be diagnosed.

        Active-call tracking prevents ``shutdown()`` from running
        while a pipeline thread is mid-execution.
        """
        result = data
        with self._plugins_lock:
            snapshot = list(self._enabled_plugins.items())
        for plugin_id, plugin in snapshot:
            if not hasattr(plugin, "process"):
                continue
            if hasattr(plugin, "_enter_active"):
                plugin._enter_active()
            try:
                transformed = plugin.process(result, direction=direction)
                if transformed is not None:
                    result = transformed
            except Exception as exc:
                _logger.exception(
                    "Plugin %s parse phase failed: %s", plugin_id, exc
                )
            finally:
                if hasattr(plugin, "_leave_active"):
                    plugin._leave_active()
        return result

    def encode_data_with_plugins(
        self, data: bytes, *, direction: str = "tx"
    ) -> bytes:
        """Run all enabled plugins' ``encode`` on *data* in sequence.

        Exceptions from individual plugins are logged with full tracebacks.
        Active-call tracking prevents ``shutdown()`` from running while a
        pipeline thread is mid-execution.
        """
        result = data
        with self._plugins_lock:
            snapshot = list(self._enabled_plugins.items())
        for plugin_id, plugin in snapshot:
            if not hasattr(plugin, "encode"):
                continue
            if hasattr(plugin, "_enter_active"):
                plugin._enter_active()
            try:
                transformed = plugin.encode(result, direction=direction)
                if transformed is not None:
                    result = transformed
            except Exception as exc:
                _logger.exception(
                    "Plugin %s encode phase failed: %s", plugin_id, exc
                )
            finally:
                if hasattr(plugin, "_leave_active"):
                    plugin._leave_active()
        return result

    def load_plugin_from_config(self, config: PluginConfig) -> Any:
        """Load a plugin using a PluginConfig dataclass."""
        return self.load_plugin(
            config.module_path,
            plugin_class_name=config.plugin_class_name,
            enabled=config.enabled,
            config=config.config or None,
        )

    def scan_directory(self, directory: str) -> list[str]:
        """Scan a directory for Python modules and attempt to load them.

        Uses ``importlib.util.spec_from_file_location`` to load plugins
        directly from arbitrary filesystem paths (e.g. ``/tmp/plugins``)
        without requiring the directory to be a package on ``sys.path``.

        Returns the list of successfully loaded plugin IDs (absolute
        file paths).
        """
        import os

        loaded: list[str] = []
        if not os.path.isdir(directory):
            return loaded
        directory = os.path.abspath(directory)
        for entry in sorted(os.listdir(directory)):
            if entry.startswith("_") or not entry.endswith(".py"):
                continue
            file_path = os.path.join(directory, entry)
            module_name = f"_scanned_plugin_{os.path.splitext(entry)[0]}"
            try:
                spec = importlib.util.spec_from_file_location(
                    module_name, file_path
                )
                if spec is None or spec.loader is None:
                    _logger.warning(
                        "Cannot create module spec for %s", file_path
                    )
                    continue
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

                plugin_class = getattr(module, "Plugin", None)
                if plugin_class is None:
                    _logger.debug(
                        "No Plugin class found in %s, skipping", file_path
                    )
                    continue

                instance = plugin_class()
                if hasattr(instance, "initialize"):
                    instance.initialize()

                with self._plugins_lock:
                    self._plugins[file_path] = instance
                    self._enabled_plugins[file_path] = instance

                loaded.append(file_path)
            except Exception as exc:
                _logger.exception(
                    "Failed to load plugin from %s: %s", file_path, exc
                )
        return loaded


def parse_data_with_plugins(
    data: bytes,
    *,
    plugins: PluginManager | None = None,
    direction: str = "rx",
) -> bytes:
    """Module-level convenience wrapper for PluginManager.parse_data_with_plugins."""
    manager = plugins or _default_plugin_manager
    return manager.parse_data_with_plugins(data, direction=direction)


def encode_data_with_plugins(
    data: bytes,
    *,
    plugins: PluginManager | None = None,
    direction: str = "tx",
) -> bytes:
    """Module-level convenience wrapper for PluginManager.encode_data_with_plugins."""
    manager = plugins or _default_plugin_manager
    return manager.encode_data_with_plugins(data, direction=direction)


_default_plugin_manager = PluginManager()