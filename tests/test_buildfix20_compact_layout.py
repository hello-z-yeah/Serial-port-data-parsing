from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "protocol_parser" / "gui.py"
MCU = ROOT / "protocol_parser" / "mcu_page.py"


def test_serial_toolbar_uses_compact_rows_and_never_stretches_start_button():
    source = GUI.read_text(encoding="utf-8")
    assert "def _reset_grid_stretches" in source
    assert "if width >= 760:" in source
    assert "self.port_combo.setMinimumWidth(180)" in source
    assert "self.baud_combo.setMaximumWidth(168)" in source
    assert "layout.setAlignment(\n            self.btn_start" in source
    assert "(self.btn_start, 2, 1, 1, 2)" not in source


def test_expanded_serial_details_only_stretch_edit_fields():
    source = GUI.read_text(encoding="utf-8")
    assert 'self.serial_detail_panel.setObjectName("serialDetailPanel")' in source
    assert "(self.save_path_edit, 1, 1, 1, 5)" in source
    assert "(self.btn_choose_path, 1, 6, 1, 1)" in source
    assert "(self.btn_open_receive_location, 1, 7, 1, 1)" in source
    # Column 5 belongs to filename/path editors; action columns 6/7 stay compact.
    assert "layout.setColumnStretch(5, 1)" in source


def test_mcu_responsive_grids_clear_stale_stretch_coefficients():
    source = MCU.read_text(encoding="utf-8")
    assert "def _reset_grid_stretches" in source
    assert source.count("self._reset_grid_stretches(layout)") >= 4
    assert "layout.setAlignment(\n                button" in source


def test_realtime_attribute_id_and_action_columns_are_readable():
    source = MCU.read_text(encoding="utf-8")
    assert "0: 48, 1: 88, 2: 150, 3: 190" in source
    assert "self._attr_wrapped_column_maximums = {2: 260, 3: 320}" in source
    assert "def _measure_attr_column_width" in source
    assert "header_view.setSectionsMovable(False)" in source
    assert "header_view.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)" in source
    assert "id_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)" in source
    assert "self.attr_table.setTextElideMode(Qt.TextElideMode.ElideNone)" in source
