"""Small layout helpers shared by extracted GUI dialogs."""
from __future__ import annotations

from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QSizePolicy, QWidget

from protocol_parser.dpi_font import UI_FONT_BASE_POINT_SIZE

ADD_SERIAL_DIALOG_MIN_WIDTH = 600
ADD_SERIAL_PORT_COMBO_MIN_WIDTH = 440


def fit_button_to_text(
    button: QWidget,
    *,
    horizontal_padding: int = 24,
    vertical_padding: int = 10,
    minimum_width: int = 0,
    minimum_height: int = 0,
) -> tuple[int, int]:
    """Size a button from its label and current font without clipping."""
    font = QFont(button.font())
    if font.pointSizeF() <= 0:
        font.setPointSize(UI_FONT_BASE_POINT_SIZE)
    metrics = QFontMetrics(font)
    text_getter = getattr(button, "text", None)
    text = str(text_getter() if callable(text_getter) else "")
    hint = button.sizeHint()
    width = max(
        minimum_width,
        int(hint.width()),
        metrics.horizontalAdvance(text) + horizontal_padding,
    )
    height = max(
        minimum_height,
        int(hint.height()),
        metrics.height() + vertical_padding,
    )
    button.setMinimumSize(width, height)
    if button.maximumWidth() < width:
        button.setMaximumWidth(16_777_215)
    if button.maximumHeight() < height:
        button.setMaximumHeight(16_777_215)
    button.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
    return width, height
