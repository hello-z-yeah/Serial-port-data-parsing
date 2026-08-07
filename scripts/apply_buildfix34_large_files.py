#!/usr/bin/env python3
"""Apply BuildFix34 residual edits to large source files (gui/mcu_page/parser).

Windows-safe pure Python. Run from repo root after pulling fix/hardening-p0-p1:

    python scripts/apply_buildfix34_large_files.py
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fix_gui() -> None:
    p = ROOT / "protocol_parser" / "gui.py"
    t = p.read_text(encoding="utf-8")
    if "QueuedConnection" in t and "storage_error_signal.connect" in t:
        # already has QueuedConnection somewhere near storage signals
        if "storage_error_signal.connect(\n            self._on_storage_error, Qt.ConnectionType.QueuedConnection" in t.replace("\r\n", "\n"):
            print("gui already OK")
            return
    old = (
        "        self.bridge.storage_error_signal.connect(self._on_storage_error)\n"
        "        self.bridge.storage_drop_signal.connect(self._on_storage_drop)\n"
    )
    new = (
        "        self.bridge.storage_error_signal.connect(\n"
        "            self._on_storage_error, Qt.ConnectionType.QueuedConnection\n"
        "        )\n"
        "        self.bridge.storage_drop_signal.connect(\n"
        "            self._on_storage_drop, Qt.ConnectionType.QueuedConnection\n"
        "        )\n"
    )
    if old not in t:
        if "QueuedConnection" in t:
            print("gui already OK")
            return
        raise SystemExit("gui pattern not found")
    p.write_text(t.replace(old, new, 1), encoding="utf-8")
    print("gui OK")


def fix_mcu() -> None:
    p = ROOT / "protocol_parser" / "mcu_page.py"
    t = p.read_text(encoding="utf-8")
    if "断开旧行控件信号并清空映射" in t:
        print("mcu already OK")
        return
    old = (
        "        self.attr_table.setUpdatesEnabled(False)\n"
        "        try:\n"
        "            self.attr_table.clearContents()\n"
    )
    new = (
        "        self.attr_table.setUpdatesEnabled(False)\n"
        "        try:\n"
        "            # 重建前断开旧行控件信号并清空映射，避免产品切换后残留引用。\n"
        "            for check in list(self._attr_select_checks.values()):\n"
        "                try:\n"
        "                    check.stateChanged.disconnect(self._on_row_select_changed)\n"
        "                except (RuntimeError, TypeError):\n"
        "                    pass\n"
        "            self.attr_table.clearContents()\n"
    )
    if old not in t:
        raise SystemExit("mcu pattern not found")
    p.write_text(t.replace(old, new, 1), encoding="utf-8")
    print("mcu OK")


def fix_parser() -> None:
    p = ROOT / "protocol_parser" / "parser.py"
    t = p.read_text(encoding="utf-8")
    if "override = copy.deepcopy(override)" in t:
        print("parser already OK")
        return
    old = (
        "    import copy\n"
        "    result = copy.deepcopy(base)\n\n"
        "    # 基本元信息\n"
    )
    new = (
        "    import copy\n"
        "    result = copy.deepcopy(base)\n"
        "    override = copy.deepcopy(override)\n\n"
        "    # 基本元信息\n"
    )
    if old not in t:
        raise SystemExit("parser pattern not found")
    t2 = t.replace(old, new, 1)
    needle = "    - enums：递归合并，override 覆盖 base\n    \"\"\""
    insert = (
        "    - enums：递归合并，override 覆盖 base\n\n"
        "    入参均 deepcopy，保证调用方传入的 base/override 不被原地污染。\n"
        "    \"\"\""
    )
    if needle in t2 and "入参均 deepcopy" not in t2:
        t2 = t2.replace(needle, insert, 1)
    p.write_text(t2, encoding="utf-8")
    print("parser OK")


if __name__ == "__main__":
    fix_gui()
    fix_mcu()
    fix_parser()
    print("done")
