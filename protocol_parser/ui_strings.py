"""User-visible chrome strings prepared for Qt translation.

Only wrap shared window chrome here: title bar actions, monitor controls,
status bar labels, and find-in-log UI. Protocol field names and enum labels
remain in source JSON and parser output.
"""
from __future__ import annotations

from protocol_parser.app_info import APP_NAME, APP_VERSION
from protocol_parser.i18n import tr

_UI_CONTEXT = "SerialX.MainWindow"


def ui_text(source: str) -> str:
    return tr(_UI_CONTEXT, source)


def window_title_suffix() -> str:
    return ui_text(f"{APP_NAME} v{APP_VERSION}")
