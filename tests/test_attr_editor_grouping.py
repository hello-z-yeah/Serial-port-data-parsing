from pathlib import Path

from protocol_parser.attr_editor import attribute_group_key

ROOT = Path(__file__).resolve().parents[1]
EDITOR = ROOT / "protocol_parser" / "attr_editor.py"


def test_attribute_group_key_splits_on_first_hyphen():
    assert attribute_group_key({"original_name": "Device Information-Enterprise Logo"}) == "Device Information"
    assert attribute_group_key({"original_name": "curtain-Motor"}) == "curtain"
    assert attribute_group_key({"original_name": "data-2"}) == "data"


def test_attribute_group_key_without_hyphen_uses_whole_name():
    assert attribute_group_key({"original_name": "data"}) == "data"


def test_attribute_group_key_falls_back_to_name():
    assert attribute_group_key({"name": "curtain-Status"}) == "curtain"


def test_attribute_group_key_empty_source():
    assert attribute_group_key({}) == "其他"


def test_attribute_editor_avoids_row_span_for_group_headers():
    source = EDITOR.read_text(encoding="utf-8")
    assert "setSpan(" not in source
    assert "AttributeEditorCellDelegate" in source
    assert "AttributeEditorTable" in source
    assert "_cleanup_stale_cell_editors" in source


def test_attribute_editor_group_header_uses_fluent_tree_style():
    source = EDITOR.read_text(encoding="utf-8")
    assert "_AttributeGroupHeader" in source
    assert "FluentIcon.CHEVRON_RIGHT_MED" in source
    assert "FluentIcon.CHEVRON_DOWN_MED" in source
    assert 'PushButton("▶"' not in source
    assert "#EEF2F7" not in source


def test_attribute_rows_use_checkbox_indent_only():
    source = EDITOR.read_text(encoding="utf-8")
    assert "_ATTR_CHECKBOX_LEFT_MARGIN" in source
    assert "_COLUMN_WIDTHS" in source
    assert "COL_SELECTED: 96" in source
    assert "ElideNone" in source
    assert "_on_attr_checkbox_clicked" in source
    assert "blockSignals(True)" in source


def test_attribute_editor_keeps_dialog_size_stable_during_edits():
    source = EDITOR.read_text(encoding="utf-8")
    assert "smstSkipTableAdaptiveGeometry" in source
    assert "_stabilize_dialog_geometry" in source
    assert "include_tables=False" in source
    assert "apply_adaptive_geometry(self.table)" not in source

    dpi = (ROOT / "protocol_parser" / "dpi_font.py").read_text(encoding="utf-8")
    assert "smstSkipTableAdaptiveGeometry" in dpi
    assert "isinstance(top, QDialog)" in dpi


def test_attribute_editor_puts_column_headers_inside_each_group():
    source = EDITOR.read_text(encoding="utf-8")
    assert "horizontalHeader().setVisible(False)" in source
    assert "_insert_group_column_header_row" in source
    assert "ROW_KIND_COLUMN_HEADER" in source
    assert "_ATTR_ROW_HEIGHT" in source


def test_attribute_editor_supports_back_to_product_json_step():
    source = EDITOR.read_text(encoding="utf-8")
    assert "allow_back" in source
    assert "back_requested" in source
    assert "_request_back" in source
    assert "_toggle_group_selection" in source
    assert "bottom.addWidget(btn_save, 1, action_column)" in source
    assert source.index("bottom.addWidget(btn_save") < source.index("bottom.addWidget(btn_back")

    mcu = (ROOT / "protocol_parser" / "mcu_page.py").read_text(encoding="utf-8")
    assert "allow_back=True" in mcu
    assert "editor.back_requested" in mcu
