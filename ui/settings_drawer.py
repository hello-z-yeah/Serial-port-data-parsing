"""设置与主题侧栏（Drawer/Settings）。

按钮对齐清单：
- [主题切换]：使用 qfluentwidgets.setTheme 实时无缝切换 Light / Dark
- [字体大小调节]：slider + spinbox 动态调节界面字体大小并持久化
- [导入 Word 协议文档]：弹窗选 .docx，调用 docx_importer 解析
- [导出日志到 Excel/CSV]：UTF-8-SIG + 中文表头
- [会话快照]：保存/恢复当前配置状态至 ~/.config/newTool
"""
from __future__ import annotations

import csv
import json
import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QGridLayout, QSizePolicy, QLabel,
)
from qfluentwidgets import (
    CardWidget, BodyLabel, StrongBodyLabel, PushButton, PrimaryPushButton,
    ComboBox, Slider, SpinBox, CheckBox, FluentIcon as FIF,
    Theme, setTheme, setThemeColor,
)

from protocol_parser.paths import user_data_path


# 偏好配置文件路径：~/.config/newTool/preferences.json（Linux）
# Win 下：USERPROFILE/.config/newTool/preferences.json
def _prefs_path() -> Path:
    home = Path.home()
    cfg_dir = home / ".config" / "newTool"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    return cfg_dir / "preferences.json"


def load_prefs() -> dict:
    p = _prefs_path()
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_prefs(prefs: dict) -> bool:
    p = _prefs_path()
    try:
        with p.open("w", encoding="utf-8") as f:
            json.dump(prefs, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def write_log_csv(path: str, lines: list) -> bool:
    """UTF-8-SIG + 中文表头，标准 CSV 导出。"""
    try:
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["序号", "时间", "方向", "命令字", "名称", "原始HEX", "字段/错误"])
            for i, ln in enumerate(lines, 1):
                w.writerow([i, "", "", "", "", ln, ""])
        return True
    except Exception:
        return False


class SettingsDrawer(CardWidget):
    """设置侧栏（嵌入主界面右侧或作为 Drawer 弹出均可）。

    信号：
      themeChanged(str)              : "light" / "dark"
      fontPointSizeChanged(int)
      importDocxRequested(str path)
      exportLogsRequested(str path, str fmt)  : fmt = "csv" / "xlsx"
      saveSnapshotRequested()
      loadSnapshotRequested()
    """

    themeChanged = Signal(str)
    fontPointSizeChanged = Signal(int)
    importDocxRequested = Signal(str)
    exportLogsRequested = Signal(str, str)
    saveSnapshotRequested = Signal()
    loadSnapshotRequested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._prefs = load_prefs()
        self._build_ui()
        self._connect_signals()
        # 启动时应用偏好
        self._apply_initial_prefs()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        layout = QGridLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = StrongBodyLabel("设置 / 主题", self)
        layout.addWidget(title, 0, 0, 1, 4)

        # 主题切换
        layout.addWidget(BodyLabel("主题:", self), 1, 0)
        self.theme_combo = ComboBox(self)
        self.theme_combo.addItem("Light (亮色)", "light")
        self.theme_combo.addItem("Dark (暗色)", "dark")
        layout.addWidget(self.theme_combo, 1, 1, 1, 3)

        # 字体大小
        layout.addWidget(BodyLabel("字体大小:", self), 2, 0)
        self.font_slider = Slider(Qt.Orientation.Horizontal, self)
        self.font_slider.setRange(8, 24)
        self.font_slider.setValue(int(self._prefs.get("font_size", 9)))
        layout.addWidget(self.font_slider, 2, 1, 1, 2)
        self.font_spin = SpinBox(self)
        self.font_spin.setRange(8, 24)
        self.font_spin.setValue(int(self._prefs.get("font_size", 9)))
        layout.addWidget(self.font_spin, 2, 3)

        # 文件 I/O 区
        layout.addWidget(StrongBodyLabel("文件导入 / 导出", self), 3, 0, 1, 4)

        self.import_docx_btn = PushButton(FIF.DOCUMENT, "导入 Word 协议文档", self)
        layout.addWidget(self.import_docx_btn, 4, 0, 1, 4)

        # 导出格式
        layout.addWidget(BodyLabel("导出日志格式:", self), 5, 0)
        self.export_fmt_combo = ComboBox(self)
        self.export_fmt_combo.addItem("CSV (UTF-8-SIG)", "csv")
        self.export_fmt_combo.addItem("Excel (.xlsx 兼容 CSV)", "xlsx")
        layout.addWidget(self.export_fmt_combo, 5, 1, 1, 3)
        self.export_logs_btn = PushButton(FIF.SAVE, "导出收发日志", self)
        layout.addWidget(self.export_logs_btn, 6, 0, 1, 4)

        # 会话快照
        layout.addWidget(StrongBodyLabel("会话快照", self), 7, 0, 1, 4)
        self.save_snapshot_btn = PushButton(FIF.SAVE, "保存当前会话", self)
        layout.addWidget(self.save_snapshot_btn, 8, 0, 1, 2)
        self.load_snapshot_btn = PushButton(FIF.FOLDER, "恢复上次会话", self)
        layout.addWidget(self.load_snapshot_btn, 8, 2, 1, 2)

        layout.setRowStretch(9, 1)

    def _connect_signals(self) -> None:
        self.theme_combo.currentIndexChanged.connect(self._on_theme_changed)
        self.font_slider.valueChanged.connect(self._on_font_slider)
        self.font_spin.valueChanged.connect(self._on_font_spin)
        self.import_docx_btn.clicked.connect(self._on_import_docx)
        self.export_logs_btn.clicked.connect(self._on_export_logs)
        self.save_snapshot_btn.clicked.connect(self.saveSnapshotRequested.emit)
        self.load_snapshot_btn.clicked.connect(self.loadSnapshotRequested.emit)

    def _apply_initial_prefs(self) -> None:
        theme = self._prefs.get("theme", "light")
        idx = 0 if theme == "light" else 1
        self.theme_combo.setCurrentIndex(idx)
        self._on_theme_changed()
        size = int(self._prefs.get("font_size", 9))
        self.font_slider.setValue(size)
        self.font_spin.setValue(size)
        self.fontPointSizeChanged.emit(size)

    # ------------------------------------------------------------------ 槽
    def _on_theme_changed(self) -> None:
        mode = self.theme_combo.currentData() or "light"
        if mode == "dark":
            setTheme(Theme.DARK)
        else:
            setTheme(Theme.LIGHT)
        setThemeColor("#0078D4")
        self.themeChanged.emit(mode)
        self._prefs["theme"] = mode
        save_prefs(self._prefs)

    def _on_font_slider(self, val: int) -> None:
        self.font_spin.blockSignals(True)
        self.font_spin.setValue(val)
        self.font_spin.blockSignals(False)
        self._apply_font_size(val)

    def _on_font_spin(self, val: int) -> None:
        self.font_slider.blockSignals(True)
        self.font_slider.setValue(val)
        self.font_slider.blockSignals(False)
        self._apply_font_size(val)

    def _apply_font_size(self, size: int) -> None:
        self.fontPointSizeChanged.emit(int(size))
        self._prefs["font_size"] = int(size)
        save_prefs(self._prefs)

    def _on_import_docx(self) -> None:
        from protocol_parser.docx_importer import check_docx_available
        if not check_docx_available():
            from qfluentwidgets import InfoBar, InfoBarPosition
            InfoBar.warning(
                title="缺少依赖",
                content="python-docx 未安装，请运行: pip install python-docx",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP,
                duration=4000,
                parent=self,
            )
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 Word 协议文档", "", "Word 文档 (*.docx)"
        )
        if path:
            self.importDocxRequested.emit(path)

    def _on_export_logs(self) -> None:
        fmt = self.export_fmt_combo.currentData() or "csv"
        ext = "csv" if fmt == "csv" else "xlsx"
        default_name = f"serial_log.{ext}"
        path, _ = QFileDialog.getSaveFileName(
            self, "导出收发日志", default_name,
            f"日志文件 (*.{ext})"
        )
        if path:
            self.exportLogsRequested.emit(path, fmt)

    # ------------------------------------------------------------------ 工具
    def current_prefs(self) -> dict:
        return dict(self._prefs)
