"""PID / Model / 功能 JSON 导入与修改对话框。"""
from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QFileDialog, QWidget, QScrollArea, QSizePolicy, QFrame
from qfluentwidgets import (
    BodyLabel, StrongBodyLabel, LineEdit, TextEdit, PushButton, PrimaryPushButton,
    CardWidget,
)

from .widgets import StyledMessageBox, apply_fluent_dialog_style
from .theme import PALETTE
from .ui_error import format_expected_user_error
from .dpi_font import apply_adaptive_geometry, fit_window_to_screen


class PlainJsonTextEdit(TextEdit):
    """JSON editor that always pastes clipboard content as plain text."""

    def insertFromMimeData(self, source) -> None:  # type: ignore[override]
        if source is not None and source.hasText():
            self.textCursor().insertText(source.text())
            return
        super().insertFromMimeData(source)


class ProductImportDialog(QDialog):
    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        product_name: str = "",
        pid: str = "",
        model: str = "",
        version: str = "1.0.0",
        json_text: str | dict | list = "",
        edit_mode: bool = False,
        allow_delete: bool = False,
    ):
        super().__init__(parent)
        apply_fluent_dialog_style(self)
        self._edit_mode = bool(edit_mode)
        self.delete_requested = False
        self.setWindowTitle("修改产品JSON" if self._edit_mode else "导入产品JSON")
        self.setMinimumSize(620, 560)
        self.resize(720, 660)
        self.setAcceptDrops(True)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        self.content_scroll = QScrollArea(self)
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        card = CardWidget()
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)

        heading = (
            "修改产品名称、PID、Model、功能 JSON，并重新选择需要的功能属性"
            if self._edit_mode
            else "通过 PID / Model / 功能定义 JSON 导入产品"
        )
        layout.addWidget(StrongBodyLabel(heading))

        self.product_name_edit = self._add_line(layout, "产品名称", "如：巴迪斯智能浴霸")
        self.pid_edit = self._add_line(layout, "PID", "设备 PID")
        self.model_edit = self._add_line(layout, "Model", "设备 Model")
        self.version_edit = self._add_line(layout, "MCU版本", "1.0.0")
        self.product_name_edit.setText(str(product_name or ""))
        self.pid_edit.setText(str(pid or ""))
        self.model_edit.setText(str(model or ""))
        self.version_edit.setText(str(version or "1.0.0"))

        layout.addWidget(BodyLabel("功能定义 JSON（可粘贴或拖入 .json 文件）："))
        self.json_edit = PlainJsonTextEdit(card)
        # JSON 输入只接受纯文本。某些网页、聊天工具或深色代码编辑器复制时
        # 会把 HTML 背景色一并放进剪贴板；QTextEdit 默认保留富文本格式，
        # 导致粘贴后出现大面积黑色背景。关闭富文本并明确浅色编辑区，
        # 不改变 JSON 内容和后续解析逻辑。
        self.json_edit.setAcceptRichText(False)
        self.json_edit.setStyleSheet(f"""
            QTextEdit {{
                color: {PALETTE['text']};
                background-color: {PALETTE['card_bg']};
                border: 1px solid {PALETTE['card_border']};
                border-radius: 6px;
                padding: 6px;
                selection-color: #FFFFFF;
                selection-background-color: {PALETTE['primary']};
            }}
            QTextEdit:focus {{
                border: 1px solid {PALETTE['primary']};
            }}
        """)
        self.json_edit.setPlaceholderText(
            '支持：①米家 services 格式  ② {"0x00": {...}} 属性字典  ③属性数组'
        )
        self.json_edit.setMinimumHeight(160)
        if isinstance(json_text, str):
            initial_json = json_text
        elif json_text:
            initial_json = json.dumps(json_text, ensure_ascii=False, indent=2)
        else:
            initial_json = ""
        self.json_edit.setPlainText(initial_json)
        layout.addWidget(self.json_edit, stretch=1)
        hint = BodyLabel("拖入 JSON 文件时会自动读取 UTF-8 内容。")
        hint.setEnabled(False)
        layout.addWidget(hint)

        buttons = QGridLayout()
        buttons.setHorizontalSpacing(8)
        buttons.setVerticalSpacing(6)
        load_button = PushButton("从文件加载")
        load_button.clicked.connect(self._load_file)
        buttons.addWidget(load_button, 0, 0)
        next_column = 1
        if allow_delete:
            delete_button = PushButton("删除当前产品")
            delete_button.setStyleSheet(
                delete_button.styleSheet()
                + "QPushButton { color: #C42B1C; }"
            )
            delete_button.clicked.connect(self._request_delete)
            buttons.addWidget(delete_button, 0, 1)
            next_column = 2
        buttons.setColumnStretch(next_column, 1)
        cancel_button = PushButton("取消")
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button, 1, max(0, next_column - 1))
        next_button = PrimaryPushButton("下一步：选择功能属性")
        next_button.clicked.connect(self._accept_checked)
        buttons.addWidget(next_button, 1, next_column)
        layout.addLayout(buttons)
        self.content_scroll.setWidget(card)
        outer.addWidget(self.content_scroll)
        apply_adaptive_geometry(self)
        fit_window_to_screen(
            self,
            preferred=(720, 660),
            minimum=(560, 460),
            margin=(36, 72),
        )

    @staticmethod
    def _add_line(layout: QVBoxLayout, label: str, placeholder: str) -> LineEdit:
        layout.addWidget(BodyLabel(label + "："))
        edit = LineEdit()
        edit.setPlaceholderText(placeholder)
        layout.addWidget(edit)
        return edit

    def _read_json_file(self, path: str) -> None:
        try:
            text = Path(path).read_text(encoding="utf-8-sig")
            self.json_edit.setPlainText(text)
            self._try_autofill_from_json(text)
        except Exception as exc:
            StyledMessageBox.warning(self, "读取失败", str(exc))

    def _try_autofill_from_json(self, text: str) -> None:
        """从 JSON 的 Base.expandRules 自动提取 model 和 PID。

        仅在对应输入框为空时填充，不覆盖用户已输入的值。
        """
        text = text.strip()
        if not text:
            return
        try:
            data = json.loads(text)
        except Exception:
            return
        if not isinstance(data, dict):
            return

        # 从 Base.expandRules / Base.version 提取。兼容 Base 大小写差异、
        # data/result 包装层以及被再次 JSON 编码的 source_function_json。
        from protocol_parser.product_importer import extract_device_info_metadata

        try:
            metadata = extract_device_info_metadata(data)
        except Exception as exc:
            StyledMessageBox.warning(self, "设备信息配置有误", str(exc))
            return
        model = str(metadata.get("model") or "").strip()
        pid = str(metadata.get("pid") or "").strip()
        version_parts = metadata.get("version") or []
        version_text = ".".join(str(part) for part in list(version_parts)[:3])

        if version_text and self.version_edit.text().strip() in ("", "1.0.0"):
            self.version_edit.setText(version_text)
        if model and not self.model_edit.text().strip():
            self.model_edit.setText(model)
        if pid and not self.pid_edit.text().strip():
            self.pid_edit.setText(pid)
        # 产品名称为空时用 model 兜底
        if model and not self.product_name_edit.text().strip():
            self.product_name_edit.setText(model)

    def _load_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择功能定义JSON", "", "JSON Files (*.json);;所有文件 (*.*)")
        if path:
            self._read_json_file(path)

    def _accept_checked(self) -> None:
        if not self.json_text.strip():
            StyledMessageBox.warning(self, "提示", "请输入功能定义 JSON")
            return

        # 在关闭导入对话框前完成 JSON 语法和支持格式校验。这样缺括号、
        # Attrs 项结构错误、attrid 越界等问题会保留在当前窗口中提示，
        # 不会被外层误认为程序故障。
        try:
            from protocol_parser.product_importer import parse_function_json

            parse_function_json(self.json_text)
        except Exception as exc:
            StyledMessageBox.warning(
                self,
                "功能JSON内容不符合要求",
                format_expected_user_error(exc),
            )
            return

        # 粘贴场景兜底：用户直接粘贴 JSON 文本时也能自动提取 model/PID
        self._try_autofill_from_json(self.json_text)
        self.accept()

    def _request_delete(self) -> None:
        self.delete_requested = True
        self.reject()

    @property
    def product_name(self) -> str:
        return self.product_name_edit.text().strip()

    @property
    def pid(self) -> str:
        return self.pid_edit.text().strip()

    @property
    def model(self) -> str:
        return self.model_edit.text().strip()

    @property
    def version(self) -> str:
        return self.version_edit.text().strip() or "1.0.0"

    @property
    def json_text(self) -> str:
        return self.json_edit.toPlainText().strip()

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if any(url.toLocalFile().lower().endswith(".json") for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event) -> None:  # type: ignore[override]
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".json"):
                self._read_json_file(path)
                event.acceptProposedAction()
                return
        event.ignore()
