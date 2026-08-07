from pathlib import Path


def test_every_application_combo_uses_font_matched_base_class() -> None:
    gui = Path("protocol_parser/gui.py").read_text(encoding="utf-8")
    mcu = Path("protocol_parser/mcu_page.py").read_text(encoding="utf-8")
    editor = Path("protocol_parser/attr_editor.py").read_text(encoding="utf-8")
    manager = Path("protocol_parser/product_manage_dialog.py").read_text(encoding="utf-8")

    assert "class DpiAwareComboBox(MatchedPopupComboBox)" in gui
    assert "class ToggleCloseEditableComboBox(MatchedPopupEditableComboBox)" in gui
    assert "self.product_combo = MatchedPopupComboBox(operation)" in mcu
    assert "class _DialogComboBox(MatchedPopupComboBox)" in editor
    assert "class _DialogEditableComboBox(MatchedPopupEditableComboBox)" in editor
    assert "self.product_combo = MatchedPopupComboBox(card)" in manager


def test_popup_font_sync_uses_combo_exact_font_and_delayed_roundmenu_passes() -> None:
    source = Path("protocol_parser/combo_font.py").read_text(encoding="utf-8")
    assert "font = QFont(combo.font())" in source
    assert "for delay_ms in (0, 12, 40)" in source
    assert "action_widget.setFont(font)" in source
    assert "view.setFont(font)" in source
    assert "font-size:" in source
