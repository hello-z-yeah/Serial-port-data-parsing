from __future__ import annotations

import ast
import re
from pathlib import Path

from protocol_parser.app_info import APP_EXE_BASENAME, APP_EXE_NAME, APP_NAME, APP_VERSION


ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_online_update_runtime_is_completely_removed() -> None:
    assert not (ROOT / "protocol_parser" / "updater.py").exists()
    runtime_files = [
        ROOT / "exe_entry.py",
        *sorted((ROOT / "protocol_parser").glob("*.py")),
    ]
    prohibited = (
        "Updater",
        "UPDATER_GITHUB_REPO",
        "check_update",
        "检查更新",
        "在线更新",
    )
    for path in runtime_files:
        text = path.read_text(encoding="utf-8")
        for token in prohibited:
            assert token not in text, f"{path.name} still contains {token!r}"


def test_product_name_and_version_are_consistent_across_build_files() -> None:
    iss = read("installer/serial_port_parser.iss")
    spec = read("serial_port_parser_fast.spec")
    version_info = read("resources/version_info.txt")
    init_text = read("protocol_parser/__init__.py")

    assert f'#define MyAppName          "{APP_NAME}"' in iss
    assert f'#define MyAppVersion       "{APP_VERSION}"' in iss
    assert f'#define MyAppExeName       "{APP_EXE_NAME}"' in iss
    assert f'name="{APP_EXE_BASENAME}"' in spec
    assert '(str(PROJECT_ROOT / "data"), "defaults/data")' in spec
    assert f"StringStruct('ProductName', '{APP_NAME}')" in version_info
    assert f"StringStruct('ProductVersion', '{APP_VERSION}')" in version_info
    assert "VERSION: str = APP_VERSION" in init_text


def test_gui_source_freezes_monitor_mode_and_uses_incremental_updates() -> None:
    gui = read("protocol_parser/gui.py")
    mcu_page = read("protocol_parser/mcu_page.py")

    assert "mcu_cfg=self._mcu_cfg if mcu_enabled else {}" in gui
    assert "on_mcu_frame=on_mcu_frame if mcu_enabled else None" in gui
    assert "primary_enabled=not mcu_enabled" in gui
    assert "self._auto_reply.set_collector(self.collector if mcu_enabled else None)" in gui
    assert "self.bridge.attr_updated_signal.emit(changed)" in gui
    assert "if cmd_int == 0x01:" in gui
    assert "result = SimpleNamespace(" in gui
    assert "self._auto_reply.last_applied_attrids" in gui
    command_branch = next(i for i, line in enumerate(gui.split('\n')) if "if cmd_int == 0x01:" in line)
    # 查找包含update_from_frame的行（可能有多行）
    update_lines = [i for i, line in enumerate(gui.split('\n')) if "update_from_frame" in line and i > command_branch]
    assert update_lines, "未找到update_from_frame调用"
    generic_update = update_lines[0]
    assert command_branch < generic_update
    assert "self.mcu_page.refresh_current_values(changed_ids or [])" in gui
    assert "self._data_flush_timer.setInterval(40)" in mcu_page
    assert "def _flush_data_batch" in mcu_page


def test_gui_source_has_async_stop_and_visible_storage_failures() -> None:
    gui = read("protocol_parser/gui.py")
    assert "collector.stop_async(" in gui
    assert "collector_stopped_signal" in gui
    assert "RawDataWriter(" in gui
    assert "storage_error_signal" in gui
    assert "storage_drop_signal" in gui
    assert "共丢弃 {stats.dropped_records} 条记录" in gui
    assert "except (ValueError, TypeError, UnicodeError)" not in gui
    assert "except UserCorrectableError as exc" in gui


def test_python_sources_remain_parseable_without_gui_dependencies() -> None:
    # AST parsing protects all GUI entry files even when PySide6 is unavailable
    # in the headless test environment.
    paths = [ROOT / "exe_entry.py", *sorted((ROOT / "protocol_parser").glob("*.py"))]
    for path in paths:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_user_correctable_gui_paths_raise_domain_exceptions() -> None:
    gui = read("protocol_parser/gui.py")
    mcu_page = read("protocol_parser/mcu_page.py")
    assert 'raise CommandValidationError("请输入命令字 CmdID")' in gui
    assert 'raise StorageOperationError(f"无法打开接收文件目录：{folder}")' in gui
    assert 'raise CommandValidationError("HEX 长度必须为偶数")' in gui
    assert 'raise ProductConfigError("产品名称不能为空")' in mcu_page
    assert 'raise ProductConfigError("产品文件已保存，但重新加载校验失败")' in mcu_page
