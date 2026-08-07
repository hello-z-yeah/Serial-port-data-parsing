from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "protocol_parser" / "mcu_page.py"


def _source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_wrapped_columns_use_content_and_available_width_caps():
    source = _source()
    assert "self._attr_wrapped_column_maximums = {2: 260, 3: 320}" in source
    assert "self._attr_wrapped_column_ratios = {2: 0.26, 3: 0.30}" in source
    assert "viewport_width = int(table.viewport().width())" in source
    assert "return max(minimum, min(adaptive_cap, measured))" in source


def test_attribute_text_wraps_without_ellipsis():
    source = _source()
    assert "self.attr_table.setWordWrap(True)" in source
    assert "self.attr_table.setTextElideMode(Qt.TextElideMode.ElideNone)" in source


def test_wrapped_rows_use_text_and_cell_widget_height():
    source = _source()
    assert "def _resize_attr_rows_to_wrapped_content" in source
    assert "QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere" in source
    assert "cell.sizeHint().height()" in source
    assert "table.resizeRowsToContents()" in source
    assert "table.setRowHeight(row, resolved_height)" in source


def test_row_height_is_recomputed_after_column_and_table_refresh():
    source = _source()
    assert "if any(column in self._attr_wrapped_column_maximums for column in selected):" in source
    assert source.count("self._resize_attr_rows_to_wrapped_content()") >= 3
