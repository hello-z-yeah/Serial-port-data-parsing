from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DPI = ROOT / "protocol_parser" / "dpi_font.py"
GUI = ROOT / "protocol_parser" / "gui.py"
MCU = ROOT / "protocol_parser" / "mcu_page.py"
IMPORT_DIALOG = ROOT / "protocol_parser" / "product_import_dialog.py"
MANAGE_DIALOG = ROOT / "protocol_parser" / "product_manage_dialog.py"


def test_application_wide_adaptive_controller_is_installed():
    dpi = DPI.read_text(encoding="utf-8")
    gui = GUI.read_text(encoding="utf-8")
    assert "class _AdaptiveUiController" in dpi
    assert "QEvent.Type.LayoutRequest" in dpi
    assert "QEvent.Type.ScreenChangeInternal" in dpi
    assert "def install_adaptive_ui_controller" in dpi
    assert "install_adaptive_ui_controller(app)" in gui


def test_windows_uses_per_monitor_v2_and_passthrough_rounding():
    gui = GUI.read_text(encoding="utf-8")
    assert "SetProcessDpiAwarenessContext" in gui
    assert "ctypes.c_void_p(-4)" in gui
    assert "QGuiApplication.setHighDpiScaleFactorRoundingPolicy" in gui
    assert "HighDpiScaleFactorRoundingPolicy.PassThrough" in gui


def test_text_geometry_measures_labels_buttons_combos_spinboxes_and_tabs():
    source = DPI.read_text(encoding="utf-8")
    assert "metrics.horizontalAdvance(text) + 36" in source
    assert "elif isinstance(widget, QLabel)" in source
    assert "metrics.boundingRect" in source
    assert "elif _is_combo_like(widget)" in source
    assert "elif _is_spin_like(widget)" in source
    assert "elif isinstance(widget, QTabBar)" in source
    assert "setUsesScrollButtons(True)" in source
    assert "setMaximumWidth(_QT_MAX_SIZE)" in source


def test_main_toolbar_and_send_panel_reflow_instead_of_clipping():
    source = GUI.read_text(encoding="utf-8")
    assert "def _update_shared_toolbar_placement" in source
    assert "def _move_shared_toolbar_below_title_bar" in source
    assert "def _relayout_top_bar" in source
    assert "def _relayout_send_panel" in source
    assert "def _relayout_send_actions" in source
    assert 'mode = "wide" if width >= 1040' in source
    send_section = source.split("def _build_send_card", 1)[1].split("def _build_status_bar", 1)[0]
    assert "setFixedHeight" not in send_section
    assert "setMinimumHeight(editor_min_height)" in send_section


def test_narrow_mcu_page_stacks_panels_and_reflows_headers():
    source = MCU.read_text(encoding="utf-8")
    assert "def _update_content_orientation" in source
    assert "Qt.Orientation.Vertical if use_vertical" in source
    assert "def _relayout_data_bar" in source
    assert "def _relayout_attr_header" in source
    assert "def _relayout_autoreply_header" in source
    assert "def _layout_common_commands" in source
    assert "columns = 3 if width >= 600 else (2 if width >= 380 else 1)" in source


def test_long_dialogs_use_resizable_scroll_areas():
    import_source = IMPORT_DIALOG.read_text(encoding="utf-8")
    manage_source = MANAGE_DIALOG.read_text(encoding="utf-8")
    for source in (import_source, manage_source):
        assert "QScrollArea" in source
        assert "setWidgetResizable(True)" in source
        assert "ScrollBarAsNeeded" in source
    assert "QGridLayout" in import_source
    assert "QGridLayout" in manage_source


def test_navigation_auto_collapses_only_for_narrow_workspaces():
    source = GUI.read_text(encoding="utf-8")
    assert "def _adapt_navigation_for_width" in source
    assert "width < 1120" in source
    assert "width >= 1320" in source
