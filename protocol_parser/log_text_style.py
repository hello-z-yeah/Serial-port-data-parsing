"""Shared log text styling without importing the main window module."""
from __future__ import annotations

from PySide6.QtGui import QFont, QFontDatabase

from . import dpi_font
from .dpi_font import UI_FONT_BASE_POINT_SIZE, make_ui_font
from .paths import resource_path
from .theme import PALETTE
from .ui_corners import CORNER_RADIUS_PX


TEXT_EDIT_FRAME_QSS = f"""
QTextEdit {{
    color: {PALETTE["text"]};
    background-color: {PALETTE["card_bg"]};
    border: 1px solid {PALETTE["card_border"]};
    border-radius: {CORNER_RADIUS_PX}px;
    padding: 6px;
}}
QTextEdit:focus {{
    border: 1px solid {PALETTE["primary"]};
}}
"""


def make_crisp_ui_font(point_size: int = UI_FONT_BASE_POINT_SIZE) -> QFont:
    return make_ui_font(point_size)


def register_bundled_log_font() -> None:
    """Register the bundled monospace font; failure keeps the system fallback."""
    try:
        font_file = resource_path("resources/fonts/MapleMono-NF-CN-Regular.ttf")
        if not font_file.is_file():
            return
        font_id = QFontDatabase.addApplicationFont(str(font_file))
        if font_id < 0:
            return
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            dpi_font.LOG_FONT_FAMILY = str(families[0])
    except Exception:
        pass


def ensure_log_font_family() -> str | None:
    if not dpi_font.LOG_FONT_FAMILY:
        register_bundled_log_font()
    return dpi_font.LOG_FONT_FAMILY


def apply_log_text_edit_style(text_edit, *, point_size: int) -> None:
    """Apply the common frame and monospace font to one log text widget."""
    text_edit.setStyleSheet(TEXT_EDIT_FRAME_QSS)
    text_edit.setFont(make_crisp_ui_font(point_size))
    family = ensure_log_font_family()
    if family:
        font = QFont(text_edit.font())
        font.setFamily(family)
        text_edit.setFont(font)
        text_edit.setStyleSheet(
            text_edit.styleSheet()
            + f'\nQTextEdit#{text_edit.objectName()} {{ font-family: "{family}"; }}'
        )
        document_font = QFont(text_edit.document().defaultFont())
        document_font.setFamily(family)
        text_edit.document().setDefaultFont(document_font)
    setter = getattr(text_edit, "set_data_font_point_size", None)
    if callable(setter):
        setter(point_size)
