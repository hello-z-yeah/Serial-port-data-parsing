"""Find-in-log UX shared by receive, monitor, and MCU pages."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QWidget,
)
from qfluentwidgets import PushButton

from protocol_parser.ui_strings import ui_text


class LogFindBar(QFrame):
    """Compact find bar shown above the main content area."""

    find_next = Signal(str)
    find_previous = Signal(str)
    closed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("LogFindBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)

        layout.addWidget(QLabel(ui_text("查找："), self))
        self.search_edit = QLineEdit(self)
        self.search_edit.setPlaceholderText(ui_text("输入 HEX、ASCII 或日志片段"))
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.returnPressed.connect(self._emit_next)
        layout.addWidget(self.search_edit, stretch=1)

        self.btn_prev = PushButton(ui_text("上一个"), self)
        self.btn_prev.clicked.connect(self._emit_previous)
        layout.addWidget(self.btn_prev)

        self.btn_next = PushButton(ui_text("下一个"), self)
        self.btn_next.clicked.connect(self._emit_next)
        layout.addWidget(self.btn_next)

        self.btn_close = PushButton(ui_text("关闭"), self)
        self.btn_close.clicked.connect(self.close_bar)
        layout.addWidget(self.btn_close)

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def focus_search(self) -> None:
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def search_term(self) -> str:
        return self.search_edit.text().strip()

    def close_bar(self) -> None:
        self.closed.emit()
        self.hide()

    def _emit_next(self) -> None:
        term = self.search_term()
        if term:
            self.find_next.emit(term)

    def _emit_previous(self) -> None:
        term = self.search_term()
        if term:
            self.find_previous.emit(term)


def find_in_text_edit(
    text_edit,
    term: str,
    *,
    backward: bool = False,
    wrap: bool = True,
) -> bool:
    """Search within a read-only log widget; returns True when a match is selected."""
    needle = str(term or "").strip()
    if not needle or text_edit is None:
        return False

    find_flags = QTextDocument.FindFlag(0)
    if backward:
        find_flags |= QTextDocument.FindFlag.FindBackward

    found = text_edit.find(needle, find_flags)
    if found:
        text_edit.ensureCursorVisible()
        return True

    if not wrap:
        return False

    cursor = text_edit.textCursor()
    if backward:
        cursor.movePosition(QTextCursor.MoveOperation.End)
    else:
        cursor.movePosition(QTextCursor.MoveOperation.Start)
    text_edit.setTextCursor(cursor)
    found = text_edit.find(needle, find_flags)
    if found:
        text_edit.ensureCursorVisible()
    return bool(found)


class LogFindController:
    """Wire Ctrl+F / F3 shortcuts and the shared find bar to the active log view."""

    def __init__(
        self,
        window: QWidget,
        bar: LogFindBar,
        text_edit_provider,
    ) -> None:
        self._window = window
        self._bar = bar
        self._text_edit_provider = text_edit_provider
        self._shortcuts: list[QShortcut] = []

        bar.find_next.connect(lambda term: self._find(term, backward=False))
        bar.find_previous.connect(lambda term: self._find(term, backward=True))
        bar.closed.connect(self._clear_status)

        for sequence, handler in (
            ("Ctrl+F", self.show),
            ("F3", lambda: self._find(self._bar.search_term(), backward=False)),
            ("Shift+F3", lambda: self._find(self._bar.search_term(), backward=True)),
            ("Esc", self.hide),
        ):
            shortcut = QShortcut(QKeySequence(sequence), window)
            shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
            shortcut.activated.connect(handler)
            self._shortcuts.append(shortcut)

    def show(self) -> None:
        text_edit = self._text_edit_provider()
        if text_edit is None:
            return
        selected = text_edit.textCursor().selectedText().replace("\u2029", "\n").strip()
        if selected and not self._bar.search_term():
            self._bar.search_edit.setText(selected)
        self._bar.show()
        self._bar.focus_search()
        if self._bar.search_term():
            self._find(self._bar.search_term(), backward=False)

    def hide(self) -> None:
        if self._bar.isVisible():
            self._bar.close_bar()

    def _find(self, term: str, *, backward: bool) -> None:
        text_edit = self._text_edit_provider()
        if text_edit is None:
            return
        if not str(term or "").strip():
            self._bar.focus_search()
            return
        if not self._bar.isVisible():
            self._bar.show()
        found = find_in_text_edit(text_edit, term, backward=backward, wrap=True)
        setter = getattr(self._window, "_set_status", None)
        if callable(setter):
            if found:
                setter(ui_text("已定位到匹配内容"))
            else:
                setter(ui_text("未找到匹配内容"))

    def _clear_status(self) -> None:
        self._bar.hide()
