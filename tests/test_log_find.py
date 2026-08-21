from __future__ import annotations

import os

import pytest
from PySide6.QtWidgets import QApplication, QTextEdit

from protocol_parser.log_find import find_in_text_edit


@pytest.fixture(scope="module", autouse=True)
def _qt_app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    yield app


def test_find_in_text_edit_wraps_and_selects_match():
    editor = QTextEdit()
    editor.setPlainText("alpha beta gamma beta")
    assert find_in_text_edit(editor, "beta", backward=False, wrap=True)
    assert "beta" in editor.textCursor().selectedText()
    assert find_in_text_edit(editor, "beta", backward=False, wrap=True)
    assert editor.textCursor().selectedText().count("beta") >= 1
