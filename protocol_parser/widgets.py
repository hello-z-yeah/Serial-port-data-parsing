"""PySide6 + qfluentwidgets 通用控件辅助。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QLabel, QWidget, QVBoxLayout
from PySide6.QtGui import QCursor
from qfluentwidgets import ToolTipFilter, ToolTipPosition


def apply_tooltip(widget: QWidget, text: str) -> None:
    """给控件加 Fluent 风格 Tooltip。"""
    if not text:
        return
    widget.setToolTip(text)
    widget.installEventFilter(ToolTipFilter(widget, showDelay=300, position=ToolTipPosition.BOTTOM))
