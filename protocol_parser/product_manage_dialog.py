"""Dialog for selecting a product JSON before editing or deleting it."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QGridLayout, QWidget, QScrollArea, QSizePolicy, QFrame
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
)

from .product_management import ProductJsonRecord
from .combo_font import MatchedPopupComboBox
from .widgets import apply_fluent_dialog_style
from .dpi_font import apply_adaptive_geometry, fit_window_to_screen


class ProductJsonManageDialog(QDialog):
    """Choose any imported JSON product, independently of the active product."""

    ACTION_EDIT = "edit"
    ACTION_DELETE = "delete"

    def __init__(
        self,
        parent: QWidget | None,
        records: list[ProductJsonRecord],
        *,
        current_product: str = "",
    ) -> None:
        super().__init__(parent)
        apply_fluent_dialog_style(self)
        self.setWindowTitle("产品JSON管理")
        self.setMinimumSize(560, 330)
        self.resize(620, 360)

        self._records = list(records)
        self._record_by_name = {record.name: record for record in self._records}
        self.requested_action = ""
        self.selected_product = ""
        self.selected_source_path: Path | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(0)
        self.content_scroll = QScrollArea(self)
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.content_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        card = CardWidget()
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(StrongBodyLabel("选择要修改或删除的产品 JSON", card))
        help_label = BodyLabel(
            "这里列出全部已导入的 JSON 产品。选择其他产品不会切换模拟 MCU 当前正在使用的产品。",
            card,
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        layout.addWidget(BodyLabel("产品：", card))
        self.product_combo = MatchedPopupComboBox(card)
        self.product_combo.setMinimumWidth(360)
        self.product_combo.addItems([record.name for record in self._records])
        self.product_combo.currentTextChanged.connect(self._refresh_details)
        layout.addWidget(self.product_combo)

        self.current_label = BodyLabel("", card)
        self.file_label = BodyLabel("", card)
        self.info_label = BodyLabel("", card)
        self.error_label = BodyLabel("", card)
        for label in (self.current_label, self.file_label, self.info_label, self.error_label):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label)

        button_row = QGridLayout()
        button_row.setHorizontalSpacing(8)
        button_row.setVerticalSpacing(6)
        cancel_button = PushButton("取消", card)
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button, 0, 0)
        self.delete_button = PushButton("删除所选产品", card)
        self.delete_button.clicked.connect(
            lambda checked=False: self._accept_action(self.ACTION_DELETE)
        )
        button_row.addWidget(self.delete_button, 0, 1)
        self.edit_button = PrimaryPushButton("修改所选产品", card)
        self.edit_button.clicked.connect(
            lambda checked=False: self._accept_action(self.ACTION_EDIT)
        )
        button_row.addWidget(self.edit_button, 1, 0, 1, 2)
        button_row.setColumnStretch(0, 1)
        button_row.setColumnStretch(1, 1)
        layout.addLayout(button_row)
        self.content_scroll.setWidget(card)
        outer.addWidget(self.content_scroll)

        if self._records:
            preferred = str(current_product or "").strip()
            index = self.product_combo.findText(preferred) if preferred else -1
            self.product_combo.setCurrentIndex(index if index >= 0 else 0)
            self._refresh_details(self.product_combo.currentText())
        else:
            self.product_combo.setEnabled(False)
            self.edit_button.setEnabled(False)
            self.delete_button.setEnabled(False)
            self.current_label.setText("当前没有可管理的产品 JSON。")

        apply_adaptive_geometry(self)
        fit_window_to_screen(
            self,
            preferred=(660, max(380, self.sizeHint().height())),
            minimum=(500, 320),
            margin=(36, 72),
        )

    def _refresh_details(self, product_name: str) -> None:
        record = self._record_by_name.get(str(product_name or "").strip())
        if record is None:
            self.selected_product = ""
            self.selected_source_path = None
            self.current_label.setText("")
            self.file_label.setText("")
            self.info_label.setText("")
            self.error_label.setText("")
            self.edit_button.setEnabled(False)
            self.delete_button.setEnabled(False)
            return

        self.selected_product = record.name
        self.selected_source_path = record.source_path
        self.current_label.setText(f"所选产品：{record.name}")
        self.file_label.setText(f"JSON 文件：{record.filename or record.source_path}")
        detail_parts = [
            f"PID：{record.pid or '未设置'}",
            f"Model：{record.model or '未设置'}",
            f"MCU版本：{record.mcu_version or '未设置'}",
            f"属性数量：{record.attribute_count}",
        ]
        self.info_label.setText("    ".join(detail_parts))
        self.error_label.setText(
            f"读取提示：{record.load_error}" if record.load_error else ""
        )
        # 文件损坏时仍允许删除，但不允许进入修改流程。
        self.edit_button.setEnabled(not bool(record.load_error))
        self.delete_button.setEnabled(True)

    def _accept_action(self, action: str) -> None:
        if not self.selected_product or self.selected_source_path is None:
            return
        self.requested_action = str(action)
        self.accept()
