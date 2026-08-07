"""PySide6 + qfluentwidgets 通用控件辅助。"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel, QWidget, QVBoxLayout, QHBoxLayout, QFrame, QPushButton,
    QButtonGroup, QDialog, QSizePolicy,
)
from qfluentwidgets import (
    ToolTipFilter, ToolTipPosition, CardWidget, StrongBodyLabel,
    BodyLabel, PrimaryPushButton,
)

from .theme import PALETTE
from .dpi_font import fit_text_control, apply_adaptive_geometry, fit_window_to_screen


def apply_tooltip(widget: QWidget, text: str) -> None:
    """给控件加 Fluent 风格 Tooltip。"""
    if not text:
        return
    widget.setToolTip(text)
    widget.installEventFilter(
        ToolTipFilter(widget, showDelay=300, position=ToolTipPosition.BOTTOM)
    )


def apply_fluent_dialog_style(dialog: QDialog) -> None:
    """统一普通 ``QDialog`` 的背景与文字颜色，避免退回系统原始灰色外观。"""
    dialog.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    dialog.setStyleSheet(f"""
        QDialog {{
            background: {PALETTE['surface']};
            color: {PALETTE['text']};
        }}
        QLabel {{
            color: {PALETTE['text']};
        }}
    """)


class TwoOptionSegmentSwitch(QFrame):
    """两个选项组成的分段滑块。"""

    valueChanged = Signal(str)

    def __init__(
        self,
        left_text: str,
        right_text: str,
        parent: QWidget | None = None,
        *,
        value: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("TwoOptionSegmentSwitch")
        self._left_text = str(left_text)
        self._right_text = str(right_text)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        self.left_button = QPushButton(self._left_text, self)
        self.right_button = QPushButton(self._right_text, self)
        for button in (self.left_button, self.right_button):
            button.setCheckable(True)
            button.setAutoExclusive(False)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumHeight(30)
            button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

        self.left_button.setObjectName("SegmentLeftButton")
        self.right_button.setObjectName("SegmentRightButton")

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.addButton(self.left_button)
        self._group.addButton(self.right_button)

        layout.addWidget(self.left_button)
        layout.addWidget(self.right_button)

        self.setStyleSheet(f"""
            QFrame#TwoOptionSegmentSwitch {{
                background: #FFFFFF;
                border: 1px solid #C8CDD5;
                border-radius: 6px;
            }}
            QPushButton#SegmentLeftButton,
            QPushButton#SegmentRightButton {{
                min-width: 76px;
                padding: 5px 12px;
                color: {PALETTE['text']};
                background: #FFFFFF;
                border: none;
                font-weight: 600;
            }}
            QPushButton#SegmentLeftButton {{
                border-top-left-radius: 5px;
                border-bottom-left-radius: 5px;
            }}
            QPushButton#SegmentRightButton {{
                border-top-right-radius: 5px;
                border-bottom-right-radius: 5px;
            }}
            QPushButton#SegmentLeftButton:hover:!checked,
            QPushButton#SegmentRightButton:hover:!checked {{
                background: #F3F6FA;
            }}
            QPushButton#SegmentLeftButton:checked,
            QPushButton#SegmentRightButton:checked {{
                color: #FFFFFF;
                background: {PALETTE['primary']};
            }}
            QPushButton#SegmentLeftButton:checked:hover,
            QPushButton#SegmentRightButton:checked:hover {{
                background: {PALETTE['primary_hover']};
            }}
        """)

        fit_text_control(self.left_button)
        fit_text_control(self.right_button)

        self.left_button.clicked.connect(
            lambda checked=False: self._select(self._left_text, emit_signal=True)
        )
        self.right_button.clicked.connect(
            lambda checked=False: self._select(self._right_text, emit_signal=True)
        )

        self.setValue(value or self._left_text, emit_signal=False)

    def _select(self, value: str, *, emit_signal: bool) -> None:
        is_left = str(value) == self._left_text
        self.left_button.setChecked(is_left)
        self.right_button.setChecked(not is_left)
        if emit_signal:
            self.valueChanged.emit(self._left_text if is_left else self._right_text)

    def value(self) -> str:
        return self._left_text if self.left_button.isChecked() else self._right_text

    def setValue(self, value: str, *, emit_signal: bool = False) -> None:
        self._select(str(value), emit_signal=emit_signal)


class _StyledMessageDialog(QDialog):
    """与主界面一致的轻量模态提示框。"""

    _KIND_COLOR = {
        "information": PALETTE["primary"],
        "warning": PALETTE["warn"],
        "critical": PALETTE["error"],
    }
    _KIND_MARK = {
        "information": "i",
        "warning": "!",
        "critical": "×",
    }

    def __init__(
        self,
        parent: QWidget | None,
        title: str,
        text: str,
        kind: str,
    ) -> None:
        super().__init__(parent)
        self.setModal(True)
        self.setWindowTitle(str(title))
        self.setMinimumWidth(380)
        self.setMaximumWidth(16_777_215)
        self.setObjectName("StyledMessageDialog")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(0)

        card = CardWidget(self)
        card.setObjectName("StyledMessageCard")
        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(18, 16, 18, 16)
        card_layout.setSpacing(14)

        title_row = QHBoxLayout()
        title_row.setSpacing(12)

        color = self._KIND_COLOR.get(kind, PALETTE["primary"])
        mark = QLabel(self._KIND_MARK.get(kind, "i"), card)
        mark.setObjectName("StyledMessageMark")
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        mark.setFixedSize(34, 34)
        mark.setStyleSheet(f"""
            QLabel#StyledMessageMark {{
                color: #FFFFFF;
                background: {color};
                border-radius: 17px;
                font-size: 20px;
                font-weight: 700;
            }}
        """)
        title_row.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)

        text_box = QVBoxLayout()
        text_box.setSpacing(8)
        title_label = StrongBodyLabel(str(title), card)
        body_label = BodyLabel(str(text), card)
        body_label.setWordWrap(True)
        body_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        text_box.addWidget(title_label)
        text_box.addWidget(body_label)
        title_row.addLayout(text_box, 1)
        card_layout.addLayout(title_row)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        ok_button = PrimaryPushButton("确定", card)
        ok_button.setMinimumWidth(88)
        ok_button.clicked.connect(self.accept)
        button_row.addWidget(ok_button)
        card_layout.addLayout(button_row)

        outer.addWidget(card)
        self.setStyleSheet(f"""
            QDialog#StyledMessageDialog {{
                background: {PALETTE['surface']};
            }}
            CardWidget#StyledMessageCard {{
                background: {PALETTE['card_bg']};
                border: 1px solid {PALETTE['card_border']};
                border-radius: 10px;
            }}
        """)
        apply_adaptive_geometry(self)
        fit_window_to_screen(
            self,
            preferred=(max(420, self.sizeHint().width()), max(220, self.sizeHint().height())),
            minimum=(380, 180),
            margin=(40, 80),
        )


class StyledMessageBox:
    """兼容 ``QMessageBox.warning/information/critical`` 的外观统一门面。"""

    @staticmethod
    def _show(parent: QWidget | None, title: str, text: str, kind: str) -> int:
        dialog = _StyledMessageDialog(parent, title, text, kind)
        return dialog.exec()

    @staticmethod
    def _reject_extra_arguments(args, kwargs) -> None:
        if args or kwargs:
            raise TypeError(
                "StyledMessageBox 仅支持 parent/title/text；"
                "如需自定义按钮，请使用原生 QMessageBox.question"
            )

    @staticmethod
    def information(parent: QWidget | None, title: str, text: str, *args, **kwargs) -> int:
        StyledMessageBox._reject_extra_arguments(args, kwargs)
        return StyledMessageBox._show(parent, title, text, "information")

    @staticmethod
    def warning(parent: QWidget | None, title: str, text: str, *args, **kwargs) -> int:
        StyledMessageBox._reject_extra_arguments(args, kwargs)
        return StyledMessageBox._show(parent, title, text, "warning")

    @staticmethod
    def critical(parent: QWidget | None, title: str, text: str, *args, **kwargs) -> int:
        StyledMessageBox._reject_extra_arguments(args, kwargs)
        return StyledMessageBox._show(parent, title, text, "critical")
