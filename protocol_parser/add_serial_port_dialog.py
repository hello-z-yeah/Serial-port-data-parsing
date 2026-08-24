"""Add-serial-port dialog extracted from gui.py."""
from __future__ import annotations

from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from qfluentwidgets import BodyLabel, CardWidget, PrimaryPushButton, PushButton, StrongBodyLabel

from protocol_parser.dpi_font import (
    UI_FONT_BASE_POINT_SIZE,
    apply_adaptive_geometry,
    fit_window_to_screen,
)
from protocol_parser.gui_combos import DpiAwareComboBox, ToggleCloseEditableComboBox
from protocol_parser.gui_layout_helpers import (
    ADD_SERIAL_DIALOG_MIN_WIDTH,
    ADD_SERIAL_PORT_COMBO_MIN_WIDTH,
    fit_button_to_text,
)
from protocol_parser.log_text_style import make_crisp_ui_font
from protocol_parser.widgets import apply_fluent_dialog_style, StyledMessageBox

QMessageBox = StyledMessageBox

class AddSerialPortDialog(QDialog):
    """与主界面一致的添加串口对话框。"""

    def __init__(self, parent: QWidget | None, ports: list[dict]):
        super().__init__(parent)
        apply_fluent_dialog_style(self)
        self.setWindowTitle("添加串口")
        self._dialog_font = make_crisp_ui_font(UI_FONT_BASE_POINT_SIZE)
        self.setFont(self._dialog_font)
        self.setMinimumSize(ADD_SERIAL_DIALOG_MIN_WIDTH, 260)
        self.resize(660, 280)
        self._ports = list(ports)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 14, 14, 14)
        outer.setSpacing(0)

        card = CardWidget(self)
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(14)

        heading = StrongBodyLabel("添加串口", card)
        heading_font = QFont(self._dialog_font)
        heading_font.setWeight(QFont.Weight.DemiBold)
        heading.setFont(heading_font)
        card_layout.addWidget(heading)

        form = QGridLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(12)

        port_label = BodyLabel("串口：", card)
        port_label.setFont(self._dialog_font)
        form.addWidget(port_label, 0, 0)
        self.port_combo = DpiAwareComboBox(card)
        self.port_combo.setFont(self._dialog_font)
        port_texts: list[str] = []
        for port in self._ports:
            device = str(port.get("device") or "")
            description = str(port.get("description") or "")
            text = f"{device} - {description}" if description and description != device else device
            port_texts.append(text)
            self.port_combo.addItem(text)
        combo_metrics = QFontMetrics(self._dialog_font)
        longest_port = max((combo_metrics.horizontalAdvance(t) for t in port_texts), default=0)
        port_combo_width = max(ADD_SERIAL_PORT_COMBO_MIN_WIDTH, min(560, longest_port + 56))
        self.port_combo.setMinimumWidth(port_combo_width)
        self.port_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        form.addWidget(self.port_combo, 0, 1)

        baud_label = BodyLabel("波特率：", card)
        baud_label.setFont(self._dialog_font)
        form.addWidget(baud_label, 1, 0)
        self.baud_combo = ToggleCloseEditableComboBox(card)
        self.baud_combo.setFont(self._dialog_font)
        self.baud_combo.addItems([
            "9600", "115200", "460800", "921600", "1000000", "2000000"
        ])
        self.baud_combo.setCurrentText("9600")
        form.addWidget(self.baud_combo, 1, 1)
        form.setColumnStretch(1, 1)
        card_layout.addLayout(form)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 4, 0, 0)
        buttons.setSpacing(8)
        buttons.addStretch(1)
        cancel_button = PushButton("取消", card)
        cancel_button.setFont(self._dialog_font)
        fit_button_to_text(cancel_button, horizontal_padding=28, vertical_padding=12, minimum_width=88)
        cancel_button.clicked.connect(self.reject)
        buttons.addWidget(cancel_button)
        ok_button = PrimaryPushButton("确定", card)
        ok_button.setFont(self._dialog_font)
        fit_button_to_text(ok_button, horizontal_padding=28, vertical_padding=12, minimum_width=88)
        ok_button.clicked.connect(self.accept)
        buttons.addWidget(ok_button)
        card_layout.addLayout(buttons)

        outer.addWidget(card)
        apply_adaptive_geometry(self, _UI_FONT_POINT_SIZE)
        fit_window_to_screen(
            self,
            preferred=(700, 320),
            minimum=(520, 260),
            margin=(36, 72),
        )

    def selected_port(self) -> str:
        return self.port_combo.currentText().split(" - ")[0].strip()

    def selected_baud(self) -> int:
        text = self.baud_combo.currentText().strip()
        try:
            value = int(text)
        except (TypeError, ValueError) as exc:
            raise ValueError("波特率必须是正整数") from exc
        if value <= 0:
            raise ValueError("波特率必须是正整数")
        return value

    def accept(self) -> None:  # type: ignore[override]
        try:
            self.selected_baud()
        except ValueError as exc:
            QMessageBox.warning(self, "提示", str(exc))
            try:
                self.baud_combo.setFocus()
                # EditableComboBox 本身就是可编辑 LineEdit。
                self.baud_combo.selectAll()
            except Exception:
                pass
            return
        super().accept()


