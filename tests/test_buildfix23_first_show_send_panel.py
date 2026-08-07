from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUI = ROOT / "protocol_parser" / "gui.py"
MCU = ROOT / "protocol_parser" / "mcu_page.py"


def test_main_window_delays_a_complete_first_show_relayout():
    source = GUI.read_text(encoding="utf-8")
    assert "def _relayout_all_panels" in source
    assert "QTimer.singleShot(0, self, self._relayout_all_panels)" in source
    assert "QTimer.singleShot(100, self, self._relayout_all_panels)" in source
    relayout = source.split("def _relayout_all_panels", 1)[1].split("def showEvent", 1)[0]
    for call in (
        "self._update_shared_toolbar_placement()",
        "self._relayout_serial_main_row()",
        "self._relayout_serial_detail_rows()",
        "self._relayout_receive_toolbars()",
        "self._relayout_send_panel(force=True)",
        "self._update_shared_page_scroll_policy()",
    ):
        assert call in relayout


def test_build_time_zero_width_has_a_wide_layout_fallback():
    source = GUI.read_text(encoding="utf-8")
    assert source.count("width = max(1, int(self.width()) - 240)") >= 2
    assert "width = max(1, int(card.contentsRect().width()))" not in source


def test_send_panel_is_editor_above_compact_actions():
    source = GUI.read_text(encoding="utf-8")
    section = source.split("def _build_send_card", 1)[1].split("def _build_status_bar", 1)[0]
    assert "editor_min_height = 72" in section
    assert section.count("setMaximumHeight(120)") >= 2
    assert "horizontal_padding=16" in section
    assert "vertical_padding=5" in section
    assert "minimum_height=30" in section
    assert "def _relayout_send_actions(self, mode: str)" in section
    assert '(mode_widget, 0, 0, 1, 1)' in section
    assert "layout.setColumnStretch(5, 1)" in section
    assert 'if mode == "narrow":' in section
    assert "layout.addWidget(self.send_center_widget, 0, 0, 1, 4)" in section
    assert "layout.addWidget(self.send_action_widget, 1, 0, 1, 4)" in section
    assert "QBoxLayout.Direction.TopToBottom" not in section.split("def _relayout_send_panel", 1)[1]


def test_mcu_page_delays_first_show_relayout_and_guards_invalid_geometry():
    source = MCU.read_text(encoding="utf-8")
    assert "def _relayout_all_mcu" in source
    assert "QTimer.singleShot(0, self, self._relayout_all_mcu)" in source
    assert "QTimer.singleShot(100, self, self._relayout_all_mcu)" in source
    assert "if splitter.contentsRect().width() <= 1:" in source
    assert "QTimer.singleShot(50, self, self._rebalance_lower_panels)" in source
    assert source.count("QTimer.singleShot(0, self, self._relayout_data_bar)") >= 1
    assert source.count("width = self._preset_ideal_width()") >= 2
    assert "width = self._attr_ideal_width()" in source
