"""Dialog for choosing the active JSON product on the MCU simulate page."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QVBoxLayout, QWidget, QSizePolicy
from qfluentwidgets import (
    BodyLabel,
    CardWidget,
    PrimaryPushButton,
    PushButton,
    StrongBodyLabel,
)

from .combo_font import MatchedPopupComboBox
from .dpi_font import fit_dialog_to_content
from .product_management import (
    ProductJsonRecord,
    product_name_counts,
    product_record_display_label,
)
from .widgets import apply_fluent_dialog_style


class ProductSelectDialog(QDialog):
    """Pick one imported JSON product to load on the MCU simulate page."""

    def __init__(
        self,
        parent: QWidget | None,
        records: list[ProductJsonRecord],
        *,
        current_product: str = "",
    ) -> None:
        super().__init__(parent)
        apply_fluent_dialog_style(self)
        self.setWindowTitle("选择产品")
        self.setMinimumSize(520, 280)

        self._records = list(records)
        self._name_counts = product_name_counts(self._records)
        self._record_by_label = {
            product_record_display_label(record, self._name_counts): record
            for record in self._records
        }
        self.selected_product = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(0)

        card = CardWidget()
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(8)
        layout.addWidget(StrongBodyLabel("选择模拟 MCU 当前产品", card))
        help_label = BodyLabel(
            "确定后将加载该产品的实时属性、动作/事件和预置命令。",
            card,
        )
        help_label.setWordWrap(True)
        layout.addWidget(help_label)

        product_row = QHBoxLayout()
        product_row.setSpacing(8)
        product_row.addWidget(BodyLabel("产品：", card))
        self.product_combo = MatchedPopupComboBox(card)
        self.product_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.product_combo.addItems(list(self._record_by_label.keys()))
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
        self.ok_button = PrimaryPushButton("确定", card)
        self.ok_button.clicked.connect(self._accept_selection)
        button_row.addWidget(self.ok_button)
        layout.addLayout(button_row)
        outer.addWidget(card)

        if self._records:
            preferred = str(current_product or "").strip()
            labels = list(self._record_by_label.keys())
            index = -1
            if preferred:
                for label, record in self._record_by_label.items():
                    if preferred in (label, record.name):
                        index = labels.index(label)
                        break
            self.product_combo.setCurrentIndex(index if index >= 0 else 0)
            self._refresh_details(self.product_combo.currentText())
        else:
            self.product_combo.setEnabled(False)
            self.ok_button.setEnabled(False)
            self.file_label.setText("当前没有可选择的 JSON 产品。")

        fit_dialog_to_content(
            self,
            preferred_width=640,
            minimum=(520, 280),
            margin=(36, 72),
        )

    def _refresh_details(self, label: str) -> None:
        record = self._record_by_label.get(str(label or "").strip())
        if record is None:
            self.selected_product = ""
            self.file_label.setText("")
            self.info_label.setText("")
            self.error_label.setText("")
            self.ok_button.setEnabled(False)
            return

        self.selected_product = str(label or "").strip()
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
        self.ok_button.setEnabled(not bool(record.load_error))

    def _accept_selection(self) -> None:
        if not self.selected_product:
            return
        self.accept()
