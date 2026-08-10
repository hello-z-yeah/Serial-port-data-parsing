"""Plugin system with safe loading, configuration, and caching.

Features:
- Prevents loading disabled plugins into enabled_plugins
- Applies configuration to plugin instance before initialization
- Uses threading.RLock to protect _cache operations
"""
from __future__ import annotations

import importlib
import threading
from typing import Any, Callable


class PluginSystem:
    """Plugin loader and manager with thread-safe caching."""

    def __init__(self):
        self._plugins: dict[str, Any] = {}
        self._enabled_plugins: dict[str, Any] = {}
        self._cache: dict[str, Any] = {}
        self._cache_lock = threading.RLock()

    @property
    def enabled_plugins(self) -> dict[str, Any]:
        return self._enabled_plugins

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
        """
        module = importlib.import_module(module_path)
        plugin_class = getattr(module, plugin_class_name)
        instance = plugin_class()

        if config is not None:
            self._apply_config(instance, config)

        if hasattr(instance, "initialize"):
            instance.initialize()

        plugin_id = module_path
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
        """Get a plugin by ID (module path)."""
        return self._plugins.get(plugin_id)

    def enable_plugin(self, plugin_id: str) -> None:
        """Move a plugin from disabled to enabled collection."""
        plugin = self._plugins.get(plugin_id)
        if plugin is not None:
            self._enabled_plugins[plugin_id] = plugin

    def disable_plugin(self, plugin_id: str) -> None:
        """Move a plugin from enabled to disabled collection."""
        self._enabled_plugins.pop(plugin_id, None)

    def unload_plugin(self, plugin_id: str) -> None:
        """Unload and remove a plugin."""
        self._enabled_plugins.pop(plugin_id, None)
        plugin = self._plugins.pop(plugin_id, None)
        if plugin is not None and hasattr(plugin, "shutdown"):
            try:
                plugin.shutdown()
            except Exception:
                pass

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
        """Return all registered plugins (both enabled and disabled)."""
        return dict(self._plugins)

    def list_enabled_plugins(self) -> dict[str, Any]:
        """Return only enabled plugins."""
        return dict(self._enabled_plugins)