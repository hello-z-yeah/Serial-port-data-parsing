from pathlib import Path


def test_baud_combo_uses_editable_combo_signals_directly() -> None:
    source = Path("protocol_parser/gui.py").read_text(encoding="utf-8")

    # qfluentwidgets EditableComboBox is itself a LineEdit.  Calling
    # baud_combo.lineEdit() caused the BuildFix24 startup crash.
    assert "self.baud_combo.returnPressed.connect" in source
    assert "self.baud_combo.editingFinished.connect" in source
    assert "self.baud_combo.lineEdit()" not in source
    assert "self.baud_combo.selectAll()" in source


def test_editable_combo_font_sync_does_not_treat_self_as_wrapped_editor() -> None:
    source = Path("protocol_parser/combo_font.py").read_text(encoding="utf-8")

    assert 'editor_attr = getattr(self, "lineEdit", None)' in source
    assert "editor is not self" in source
