from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "protocol_parser" / "mcu_page.py"


def _source() -> str:
    return SOURCE.read_text(encoding="utf-8")


def test_mcu_font_controls_are_hidden_but_logic_is_retained():
    source = _source()
    assert "self.data_font_label.hide()" in source
    assert "self.data_font_spin.hide()" in source
    assert "self.data_font_spin.valueChanged.connect(" in source
    assert "self.data_text.set_data_font_point_size(self.data_font_spin.value())" in source
    data_widgets = source.split("self._data_bar_widgets = (", 1)[1].split(")", 1)[0]
    assert "data_font_label" not in data_widgets
    assert "data_font_spin" not in data_widgets


def test_data_bar_no_longer_allocates_rows_for_hidden_font_controls():
    source = _source()
    section = source.split("def _relayout_data_bar", 1)[1].split("def _relayout_attr_header", 1)[0]
    assert "self.data_title_label" in section
    assert "self.clear_button" in section
    assert "self.autoscroll_button" in section
    assert "self.data_font_label" not in section
    assert "self.data_font_spin" not in section


def test_wrapped_attribute_height_uses_exact_document_layout_and_delayed_recheck():
    source = _source()
    assert "def _wrapped_attr_text_height" in source
    assert "QTextDocument()" in source
    assert "WrapAtWordBoundaryOrAnywhere" in source
    assert "table.resizeRowsToContents()" in source
    assert "text_height + max(22, line_height // 2 + 16)" in source
    assert "def _schedule_attr_row_resize" in source
    assert "self._attr_row_resize_timer.setInterval(80)" in source
    assert source.count("self._schedule_attr_row_resize(0)") >= 3


def test_name_and_property_columns_are_content_measured_with_adaptive_wrap_width():
    source = _source()
    assert "self._attr_wrapped_column_maximums = {2: 260, 3: 320}" in source
    assert "min(adaptive_cap, measured)" in source
    assert "available_width = max(24, table.columnWidth(column) - 36)" in source
    assert "WrappedAttributeTextDelegate" in source
