from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DPI = ROOT / "protocol_parser" / "dpi_font.py"
GUI = ROOT / "protocol_parser" / "gui.py"
MCU = ROOT / "protocol_parser" / "mcu_page.py"
EDITOR = ROOT / "protocol_parser" / "attr_editor.py"


def test_application_font_uses_native_dpi_point_size_and_screen_safe_geometry() -> None:
    dpi = DPI.read_text(encoding="utf-8")
    gui = GUI.read_text(encoding="utf-8")
    assert "return 1.0" in dpi
    assert "fit_window_to_screen" in dpi
    assert "availableGeometry" in dpi
    assert "font-size: {point_size}pt" in dpi
    assert "apply_application_font(screen=app.primaryScreen())" in gui
    assert "handle.screenChanged.connect(self._on_window_screen_changed)" in gui
    assert "apply_application_font(self)" in gui


def test_mcu_attribute_and_preset_panels_receive_explicit_dpi_font() -> None:
    source = MCU.read_text(encoding="utf-8")
    assert "def apply_dpi_metrics" in source
    assert "apply_scoped_font(self, resolved)" in source
    assert "for table in (self.attr_table, self.poweron_table, self.autoreply_table)" in source
    assert "apply_table_font(table, font" in source
    assert "self._attr_base_row_height = max(38, metrics.height() + 16)" in source
    assert "self._preset_row_height = max(34, metrics.height() + 14)" in source
    assert "QEvent.Type.ScreenChangeInternal" in source


def test_data_text_remains_independent_from_whole_page_font() -> None:
    dpi = DPI.read_text(encoding="utf-8")
    mcu = MCU.read_text(encoding="utf-8")
    assert 'self.data_text.setProperty("smstIndependentDataFont", True)' in mcu
    assert 'if child.property("smstIndependentDataFont")' in dpi
    section = dpi.split("selectors = [", 1)[1].split("]", 1)[0]
    assert "QTextEdit" not in section


def test_attribute_editor_does_not_double_multiply_dpi() -> None:
    source = EDITOR.read_text(encoding="utf-8")
    assert "responsive_point_size(self, maximum=14)" in source
    assert "logicalDotsPerInch()) / 96.0" not in source
    assert "dlg.setFixedSize(420, 380)" not in source


def test_no_fixed_ten_point_application_stylesheet_remains() -> None:
    source = GUI.read_text(encoding="utf-8")
    assert 'font-size: {_UI_FONT_POINT_SIZE}pt' not in source
    assert "table_font = _make_crisp_ui_font(body_point_size)" in source
    assert "mcu_page.apply_dpi_metrics(body_point_size)" in source
