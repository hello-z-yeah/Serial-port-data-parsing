from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "protocol_parser" / "gui.py"
MCU = ROOT / "protocol_parser" / "mcu_page.py"
DPI = ROOT / "protocol_parser" / "dpi_font.py"


def test_main_window_minimum_and_receive_scroll_fallback():
    source = GUI.read_text(encoding="utf-8")
    assert "minimum=(1000, 640)" in source
    assert "self.setMinimumSize(1000, 640)" not in source
    assert 'self.shared_page_scroll = QScrollArea(wrapper)' in source
    assert "Qt.ScrollBarPolicy.ScrollBarAsNeeded" in source
    assert "Qt.ScrollBarPolicy.ScrollBarAlwaysOff" in source
    assert "def _update_shared_page_scroll_policy" in source


def test_attribute_table_all_columns_are_fixed_and_not_user_resizable():
    source = MCU.read_text(encoding="utf-8")
    assert "for column in range(self.attr_table.columnCount())" in source
    assert "header_view.setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)" in source
    assert "header_view.setSectionsMovable(False)" in source
    assert "header_view.setCascadingSectionResizes(False)" in source


def test_attribute_column_width_is_content_measured_with_scroll_fallback():
    source = MCU.read_text(encoding="utf-8")
    assert "def _measure_attr_column_width" in source
    assert "metrics.horizontalAdvance" in source
    assert "cell.sizeHint().width()" in source
    assert "cell.minimumSizeHint().width()" in source
    assert "self.attr_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)" in source
    assert "self.attr_card.setMinimumWidth(min(self._attr_ideal_width(), 320))" in source


def test_current_value_column_remeasure_is_debounced():
    source = MCU.read_text(encoding="utf-8")
    assert "self._attr_column_remeasure_timer.setInterval(250)" in source
    assert "def _schedule_attr_column_remeasure" in source
    assert "self._attr_column_remeasure_timer.start(250)" in source
    assert "self._schedule_attr_column_remeasure(6)" in source


def test_preset_tables_protect_embedded_controls_and_can_scroll():
    source = MCU.read_text(encoding="utf-8")
    assert "def _apply_preset_table_widths" in source
    assert "self._measure_table_column_width(poweron, 2, 92)" in source
    assert source.count("Qt.ScrollBarPolicy.ScrollBarAsNeeded") >= 4


def test_font_scaling_stays_point_based_and_fixed_at_base_size():
    dpi_source = DPI.read_text(encoding="utf-8")
    project_sources = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (ROOT / "protocol_parser").glob("*.py")
    )
    assert "return max(1, min(int(maximum), int(base)))" in dpi_source
    assert "setPixelSize" not in project_sources
