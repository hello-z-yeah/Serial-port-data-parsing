"""命令库视图面板（Command Library Panel）。

按钮对齐清单：
- [命令库视图]：显示命令表格（QTableWidget），点击命令可一键填入发送区

数据来源：当前协议 cfg.commands 列表（V3.0 双向命令）+ data/cmdlib/*.json 历史收藏。
点击行时把命令的 cmd_code 拼成 HEX 帧示例（如 'A5 A5 03 20 00 00'）填入 TX 发送区。
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import (
    QGridLayout, QHBoxLayout, QHeaderView, QAbstractItemView,
    QTableWidgetItem, QTableWidget, QSizePolicy,
)
from qfluentwidgets import (
    CardWidget, BodyLabel, StrongBodyLabel, PushButton, PrimaryPushButton,
    LineEdit, ComboBox,
)

from protocol_parser import get_builtin_v3, load_protocol, merge_protocol
from protocol_parser.paths import user_data_path


# 内置 cmdlib 目录
def _cmdlib_dir() -> Path:
    """data/cmdlib 目录（与旧版 gui.py 对齐：开发模式 = 项目根 data/cmdlib，
    打包模式 = user_data_path/cmdlib）。"""
    try:
        if getattr(__import__("sys"), "frozen", False):
            d = user_data_path("cmdlib")
        else:
            d = Path(__file__).resolve().parent.parent / "data" / "cmdlib"
        d.mkdir(parents=True, exist_ok=True)
        return d
    except Exception:
        return Path("data/cmdlib")


class CmdLibraryPanel(CardWidget):
    """命令库视图面板。

    信号：
      cmdSelected(hex_payload: str, mode: str)  : 双击/选中命令时发出，
                                                  mode = "hex"
      refreshRequested()
    """

    cmdSelected = Signal(str, str)
    refreshRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._cfg: dict = get_builtin_v3()
        self._build_ui()
        self._connect_signals()
        self.reload_table()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QGridLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        head = QHBoxLayout()
        head.addWidget(StrongBodyLabel("命令库", self))
        head.addStretch(1)
        self.source_combo = ComboBox(self)
        self.source_combo.addItem("协议命令", "protocol")
        self.source_combo.addItem("HEX 收藏", "hex_cmds")
        self.source_combo.addItem("ASCII 收藏", "ascii_cmds")
        head.addWidget(self.source_combo)
        self.add_to_lib_btn = PushButton("收藏当前", self)
        head.addWidget(self.add_to_lib_btn)
        self.reload_btn = PushButton("刷新", self)
        head.addWidget(self.reload_btn)
        layout.addLayout(head, 0, 0, 1, 4)

        # 表格
        self.table = QTableWidget(self)
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(["命令字", "名称", "示例 HEX"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1, 0, 1, 4)

        # 双击发送
        self.send_selected_btn = PrimaryPushButton("发送选中命令 →", self)
        layout.addWidget(self.send_selected_btn, 2, 0, 1, 4)

    def _connect_signals(self) -> None:
        self.reload_btn.clicked.connect(self.reload_table)
        self.reload_btn.clicked.connect(self.refreshRequested.emit)
        self.source_combo.currentIndexChanged.connect(self.reload_table)
        self.send_selected_btn.clicked.connect(self._emit_selected)
        self.table.doubleClicked.connect(self._emit_selected)

    # ------------------------------------------------------------------ 数据
    def set_protocol_cfg(self, cfg: dict) -> None:
        """外部（MainWindow）更新当前协议配置。"""
        self._cfg = cfg or {}
        if self.source_combo.currentData() == "protocol":
            self.reload_table()

    def reload_table(self) -> None:
        src = self.source_combo.currentData() or "protocol"
        self.table.setRowCount(0)
        if src == "protocol":
            self._load_protocol_commands()
        else:
            self._load_cmdlib_file(f"{src}.json")

    def _load_protocol_commands(self) -> None:
        cmds = self._cfg.get("commands", []) or []
        frame_cfg = self._cfg.get("frame", {}) or {}
        header_hex = self._header_hex_str(frame_cfg)
        ver_hex = self._ver_hex_str(frame_cfg)
        for cmd in cmds:
            cmd_code = cmd.get("cmd_code", "")
            try:
                cc_int = int(str(cmd_code), 0) & 0xFF if cmd_code else 0
                cc_hex = f"{cc_int:02X}"
            except Exception:
                cc_hex = str(cmd_code)
            name = cmd.get("name", "")
            # 示例 HEX：header + ver + cmd + length(0000) + chk(00)
            sample = f"{header_hex} {ver_hex} {cc_hex} 00 00 00"
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(cc_hex))
            self.table.setItem(row, 1, QTableWidgetItem(name))
            self.table.setItem(row, 2, QTableWidgetItem(sample))

    def _load_cmdlib_file(self, filename: str) -> None:
        p = _cmdlib_dir() / filename
        if not p.exists():
            return
        try:
            with p.open("r", encoding="utf-8") as f:
                items = json.load(f)
        except Exception:
            items = []
        for it in items:
            if not isinstance(it, dict):
                continue
            name = it.get("name", "")
            payload = it.get("payload", "")
            if not payload:
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(it.get("id", "")[:8]))
            self.table.setItem(row, 1, QTableWidgetItem(name))
            self.table.setItem(row, 2, QTableWidgetItem(payload))

    @staticmethod
    def _header_hex_str(frame_cfg: dict) -> str:
        h = frame_cfg.get("header", "0xA5A5")
        try:
            v = int(str(h), 0)
            size = int(frame_cfg.get("header_size", 2))
            return " ".join(f"{(v >> (8*(size-1-i))) & 0xFF:02X}" for i in range(size))
        except Exception:
            return "A5 A5"

    @staticmethod
    def _ver_hex_str(frame_cfg: dict) -> str:
        v = frame_cfg.get("ver", "0x03")
        try:
            return f"{int(str(v), 0) & 0xFF:02X}"
        except Exception:
            return "03"

    # ------------------------------------------------------------------ 槽
    def _emit_selected(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 2)
        if item is None:
            return
        payload = item.text().strip()
        mode = "hex"
        if self.source_combo.currentData() == "ascii_cmds":
            mode = "ascii"
        self.cmdSelected.emit(payload, mode)

    # ------------------------------------------------------------------ 收藏
    def save_current_to_lib(self, payload: str, name: str = "", kind: str = "hex") -> bool:
        """把当前 TX 输入框的内容存到 cmdlib。"""
        if not payload:
            return False
        filename = "hex_cmds.json" if kind == "hex" else "ascii_cmds.json"
        p = _cmdlib_dir() / filename
        items = []
        if p.exists():
            try:
                with p.open("r", encoding="utf-8") as f:
                    items = json.load(f)
            except Exception:
                items = []
        items.append({
            "id": uuid.uuid4().hex,
            "name": name or payload[:16],
            "payload": payload,
        })
        try:
            with p.open("w", encoding="utf-8") as f:
                json.dump(items, f, ensure_ascii=False, indent=2)
        except Exception:
            return False
        if self.source_combo.currentData() == (kind + "_cmds"):
            self.reload_table()
        return True
