from __future__ import annotations

from pathlib import Path

from protocol_parser.app_info import APP_VERSION
from protocol_parser.i18n import tr
from protocol_parser.serial_collector_optimized import OptimizedSerialCollector


ROOT = Path(__file__).resolve().parents[1]


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_detached_pages_do_not_call_main_window_private_api():
    mcu = _read("protocol_parser/mcu_page.py")
    receive = _read("protocol_parser/receive_page.py")
    assert "self._mw._" not in mcu
    assert 'getattr(self._mw, "_' not in mcu
    assert "self._mw._" not in receive
    assert 'getattr(self._mw, "_' not in receive
    assert "AppHostProtocol" in mcu


def test_monitor_page_does_not_import_main_window():
    monitor = _read("protocol_parser/monitor_page.py")
    assert "from protocol_parser.gui import" not in monitor
    assert "apply_log_text_edit_style" in monitor


def test_all_production_serial_entry_points_use_one_collector():
    gui = _read("protocol_parser/gui.py")
    monitor = _read("protocol_parser/monitor.py")
    package_init = _read("protocol_parser/__init__.py")
    assert "OptimizedSerialCollector as SerialCollector" in gui
    assert "OptimizedSerialCollector as SerialCollector" in monitor
    assert '(".serial_collector_optimized", "OptimizedSerialCollector")' in package_init
    for method in ("send", "send_raw", "stop_async"):
        assert callable(getattr(OptimizedSerialCollector, method))


def test_shortcuts_and_accessibility_labels_are_installed():
    gui = _read("protocol_parser/gui.py")
    log_find = _read("protocol_parser/log_find.py")
    for sequence in ("F5", "Shift+F5", "Ctrl+L", "Ctrl+S", "Ctrl+Shift+E"):
        assert f'("{sequence}",' in gui
    assert '"Ctrl+F"' in log_find
    assert '"F3"' in log_find
    assert "setAccessibleName" in gui
    assert "开始或停止串口监控" in gui
    assert "监听命中记录" in gui


def test_qt_translation_bootstrap_is_active():
    gui = _read("protocol_parser/gui.py")
    assert "install_translator(app)" in gui
    assert "ui_text(" in gui
    assert tr("test", "开始监控") == "开始监控"


def test_log_find_is_wired_into_main_window():
    gui = _read("protocol_parser/gui.py")
    assert "LogFindController" in gui
    assert "LogFindBar" in gui
    assert "_active_log_text_edit" in gui


def test_production_modules_do_not_import_plugin_system():
    for relative in (
        "protocol_parser/gui.py",
        "protocol_parser/parser.py",
        "protocol_parser/monitor.py",
        "protocol_parser/__init__.py",
    ):
        source = _read(relative)
        assert "plugin_system" not in source


def test_plugin_system_is_marked_test_only():
    source = _read("protocol_parser/plugin_system.py")
    assert "PRODUCTION_WIRED = False" in source
    assert "unit tests only" in source


def test_user_facing_docs_match_current_version_and_update_behavior():
    readme = _read("README.md")
    guide = _read("USER_GUIDE.md")
    build_guide = _read("WINDOWS_BUILD_GUIDE.md")
    assert readme.startswith(f"# SerialX {APP_VERSION}")
    assert guide.startswith(f"# SerialX 用户说明书 v{APP_VERSION}")
    assert f"SerialXSetup{APP_VERSION}_x64.exe" in readme
    assert f"SerialXSetup{APP_VERSION}_x64.exe" in guide
    assert f"SerialXSetup{APP_VERSION}_x64.exe" in build_guide
    assert "软件不联网，无自动更新" not in guide
    assert "SHA-256" in guide
    assert "监听工具页" in guide
