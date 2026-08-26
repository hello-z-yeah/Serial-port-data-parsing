"""Dialog for selecting a product JSON before editing or deleting it."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QWidget, QSizePolicy
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
from .dpi_font import fit_dialog_to_content


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
        self.setMinimumSize(520, 280)

        self._records = list(records)
        self._record_by_name = {record.name: record for record in self._records}
        self.requested_action = ""
        self.selected_product = ""
        self.selected_source_path: Path | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(0)

        card = CardWidget()
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        layout.addWidget(StrongBodyLabel("选择要修改或删除的产品 JSON", card))
        help_label = BodyLabel(
            "这里列出全部已导入的 JSON 产品。选择其他产品不会切换模拟 MCU 当前正在使用的产品。",
            card,
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        product_row = QHBoxLayout()
        product_row.setSpacing(8)
        product_label = BodyLabel("产品：", card)
        product_row.addWidget(product_label)
        self.product_combo = MatchedPopupComboBox(card)
        self.product_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.product_combo.addItems([record.name for record in self._records])
        self.product_combo.currentTextChanged.connect(self._refresh_details)
        product_row.addWidget(self.product_combo, 1)
        layout.addLayout(product_row)

        self.file_label = BodyLabel("", card)
        self.info_label = BodyLabel("", card)
        self.error_label = BodyLabel("", card)
        for label in (self.file_label, self.info_label, self.error_label):
            label.setWordWrap(True)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            layout.addWidget(label)

        button_row = QHBoxLayout()
        button_row.setSpacing(8)
        button_row.addStretch(1)
        cancel_button = PushButton("取消", card)
        cancel_button.clicked.connect(self.reject)
        button_row.addWidget(cancel_button)
        self.delete_button = PushButton("删除所选产品", card)
        self.delete_button.clicked.connect(
            lambda _checked=False: self._accept_action(self.ACTION_DELETE)
        )
        button_row.addWidget(self.delete_button)
        self.edit_button = PrimaryPushButton("修改所选产品", card)
        self.edit_button.clicked.connect(
            lambda _checked=False: self._accept_action(self.ACTION_EDIT)
        )
        button_row.addWidget(self.edit_button)
        layout.addLayout(button_row)
        outer.addWidget(card)

        if self._records:
            preferred = str(current_product or "").strip()
            index = self.product_combo.findText(preferred) if preferred else -1
            self.product_combo.setCurrentIndex(index if index >= 0 else 0)
            self._refresh_details(self.product_combo.currentText())
        else:
            self.product_combo.setEnabled(False)
            self.edit_button.setEnabled(False)
            self.delete_button.setEnabled(False)
            self.file_label.setText("当前没有可管理的产品 JSON。")

        fit_dialog_to_content(
            self,
            preferred_width=640,
            minimum=(520, 280),
            margin=(36, 72),
        )

    def _refresh_details(self, product_name: str) -> None:
        record = self._record_by_name.get(str(product_name or "").strip())
        if record is None:
            self.selected_product = ""
            self.selected_source_path = None
            self.file_label.setText("")
            self.info_label.setText("")
            self.error_label.setText("")
            self.edit_button.setEnabled(False)
            self.delete_button.setEnabled(False)
            return

        self.selected_product = record.name
        self.selected_source_path = record.source_path
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
        self.edit_button.setEnabled(not bool(record.load_error))
        self.delete_button.setEnabled(True)

    def _accept_action(self, action: str) -> None:
        if not self.selected_product or self.selected_source_path is None:
            return
        self.requested_action = str(action)
        self.accept()
