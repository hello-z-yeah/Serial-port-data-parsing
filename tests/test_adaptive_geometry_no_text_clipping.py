from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DPI = ROOT / "protocol_parser" / "dpi_font.py"
GUI = ROOT / "protocol_parser" / "gui.py"
MCU = ROOT / "protocol_parser" / "mcu_page.py"
EDITOR = ROOT / "protocol_parser" / "attr_editor.py"
WIDGETS = ROOT / "protocol_parser" / "widgets.py"


def test_native_qt_dpi_is_not_multiplied_again_from_resolution():
    source = DPI.read_text(encoding="utf-8")
    assert "def effective_resolution_scale" in source
    assert "return 1.0" in source
    assert "width_ratio" not in source
    assert "QT_SCALE_FACTOR_ROUNDING_POLICY" not in source


def test_all_text_controls_are_repolished_from_font_metrics():
    source = DPI.read_text(encoding="utf-8")
    assert "def fit_text_control" in source
    assert "def apply_adaptive_geometry" in source
    assert "metrics.horizontalAdvance(text)" in source
    assert "widget.setMinimumSize(req_w, req_h)" in source
    assert "adapt_table_geometry" in source


def test_main_window_fits_current_logical_work_area_and_uses_passthrough_rounding():
    source = GUI.read_text(encoding="utf-8")
    assert "fit_window_to_screen(" in source
    assert 'QT_SCALE_FACTOR_ROUNDING_POLICY", "PassThrough"' in source
    assert "self.setMinimumSize(1100, 700)" not in source
    assert "apply_adaptive_geometry(self, body_point_size)" in source


def test_long_control_rows_reflow_instead_of_squeezing_captions():
    gui = GUI.read_text(encoding="utf-8")
    mcu = MCU.read_text(encoding="utf-8")
    assert "def _relayout_serial_main_row" in gui
    assert "def _relayout_serial_detail_rows" in gui
    assert "def _relayout_receive_toolbars" in gui
    assert "def _relayout_operation_bar" in mcu
    assert "QBoxLayout.Direction.TopToBottom" in gui


def test_tables_and_dialogs_keep_full_text_accessible():
    mcu = MCU.read_text(encoding="utf-8")
    editor = EDITOR.read_text(encoding="utf-8")
    widgets = WIDGETS.read_text(encoding="utf-8")
    assert "ScrollBarAsNeeded" in mcu
    assert "item.setToolTip(text)" in mcu
    assert "ScrollBarAsNeeded" in editor
    assert "item.setToolTip(item.text())" in editor
    assert "fit_window_to_screen" in widgets
