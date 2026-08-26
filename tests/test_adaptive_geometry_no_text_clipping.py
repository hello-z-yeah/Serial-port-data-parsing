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


def test_mcu_page_title_and_current_product_share_same_row():
    """模拟MCU工具页：'模拟MCU工具' 粗体标题必须和 '当前产品' 下拉在同一行。

    验证 _relayout_operation_bar 在宽屏模式下：
    1. page_title_label (模拟MCU工具) 与 product_label (当前产品) 都在 row 0
    2. page_title_label 在 product_label 左侧（col 0 < col 1）
    3. page_title_label 字体是粗体
    4. 两者间有视觉间距（contentsMargins.right）
    """
    from PySide6.QtGui import QFont
    from qfluentwidgets import setTheme, Theme

    from conftest import ensure_qt_app
    from protocol_parser.gui import ProtocolParserApp, setThemeColor, PALETTE

    app = ensure_qt_app()
    setTheme(Theme.LIGHT)
    setThemeColor(PALETTE["primary"])

    mw = ProtocolParserApp()
    # 设置足够宽的窗口
    mw.resize(1400, 900)
    mw.show()
    for _ in range(10):
        app.processEvents()

    mcu = getattr(mw, "mcu_page", None)
    assert mcu is not None, "mcu_page 必须存在"

    title_label = getattr(mcu, "page_title_label", None)
    assert title_label is not None
    assert title_label.text() == "模拟MCU工具", (
        f"标题文字应为'模拟MCU工具'，实际'{title_label.text()}'"
    )

    product_label = getattr(mcu, "product_label", None)
    assert product_label is not None
    assert product_label.text().startswith("当前产品"), (
        f"产品标签应以'当前产品'开头，实际'{product_label.text()}'"
    )

    # 检查标题是粗体
    title_font = title_label.font()
    is_bold = title_font.weight() == QFont.Weight.Bold
    assert is_bold, (
        f"标题应为粗体，当前 weight={title_font.weight()}，"
        f"Bold weight={QFont.Weight.Bold}"
    )

    # 强制触发 relayout
    mcu._relayout_operation_bar()
    app.processEvents()

    operation_layout = getattr(mcu, "operation_layout", None)
    assert operation_layout is not None

    # 获取两个 widget 的位置，确认在同一行
    title_index = operation_layout.indexOf(title_label)
    product_index = operation_layout.indexOf(product_label)
    assert title_index >= 0, "page_title_label 必须在 operation_layout 中"
    assert product_index >= 0, "product_label 必须在 operation_layout 中"

    title_pos = operation_layout.getItemPosition(title_index)
    product_pos = operation_layout.getItemPosition(product_index)

    title_row, title_col, _, _ = title_pos
    product_row, product_col, _, _ = product_pos

    assert title_row == product_row, (
        f"标题和当前产品必须在同一行，"
        f"标题在 row {title_row}，当前产品在 row {product_row}"
    )
    assert title_col < product_col, (
        f"标题必须在当前产品左侧，"
        f"标题在 col {title_col}，当前产品在 col {product_col}"
    )

    # 检查视觉间距：标题右边距 > 0
    margins = title_label.contentsMargins()
    assert margins.right() > 0, (
        f"标题右边距应 > 0（用于与'当前产品'分隔），"
        f"当前右边距={margins.right()}"
    )

    # 检查分隔线在标题和当前产品之间，且在同一行
    separator = getattr(mcu, "page_separator", None)
    assert separator is not None, "page_separator 必须存在"
    separator_index = operation_layout.indexOf(separator)
    assert separator_index >= 0, "page_separator 必须在 operation_layout 中"
    sep_row, sep_col, _, _ = operation_layout.getItemPosition(separator_index)
    assert sep_row == title_row, (
        f"分隔线必须与标题在同一行，分隔线在 row {sep_row}，标题在 row {title_row}"
    )
    assert title_col < sep_col < product_col, (
        f"分隔线必须在标题和当前产品之间，"
        f"标题 col={title_col}，分隔线 col={sep_col}，当前产品 col={product_col}"
    )


def test_tables_and_dialogs_keep_full_text_accessible():
    mcu = MCU.read_text(encoding="utf-8")
    editor = EDITOR.read_text(encoding="utf-8")
    widgets = WIDGETS.read_text(encoding="utf-8")
    assert "ScrollBarAsNeeded" in mcu
    assert "item.setToolTip(text)" in mcu
    assert "ScrollBarAsNeeded" in editor
    assert "item.setToolTip(item.text())" in editor
    assert "fit_dialog_to_content" in widgets
