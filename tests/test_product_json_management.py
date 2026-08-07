from __future__ import annotations

import json
from pathlib import Path

from protocol_parser.product_management import collect_product_json_records


def _write_product(path: Path, name: str, pid: str, model: str, attrs: int) -> None:
    payload = {
        "product": name,
        "import_source": "json",
        "product_info": {
            "pid": pid,
            "model": model,
            "mcu_version": [1, 2, 3],
        },
        "attributes": {
            f"0x{index:02X}": {"name": f"attr-{index}", "typeid": 2}
            for index in range(attrs)
        },
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_manager_collects_every_json_product_not_only_active(tmp_path: Path):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    word = tmp_path / "word.json"
    _write_product(first, "当前产品", "100", "current.model", 2)
    _write_product(second, "其他产品", "200", "other.model", 3)
    _write_product(word, "Word产品", "300", "word.model", 1)

    records = collect_product_json_records(
        {
            "当前产品": str(first),
            "其他产品": str(second),
            "Word产品": str(word),
        },
        {
            "当前产品": "json",
            "其他产品": "json",
            "Word产品": "word",
        },
    )

    by_name = {record.name: record for record in records}
    assert set(by_name) == {"当前产品", "其他产品"}
    assert by_name["其他产品"].pid == "200"
    assert by_name["其他产品"].model == "other.model"
    assert by_name["其他产品"].mcu_version == "1.2.3"
    assert by_name["其他产品"].attribute_count == 3


def test_broken_json_remains_selectable_for_deletion(tmp_path: Path):
    broken = tmp_path / "broken.json"
    broken.write_text("{broken", encoding="utf-8")

    records = collect_product_json_records(
        {"损坏产品": str(broken)},
        {"损坏产品": "json"},
    )

    assert len(records) == 1
    assert records[0].name == "损坏产品"
    assert records[0].source_path == broken
    assert records[0].load_error


def test_mcu_page_uses_selected_product_and_preserves_active_context():
    source = Path("protocol_parser/mcu_page.py").read_text(encoding="utf-8")
    assert "ProductJsonManageDialog" in source
    assert "_edit_selected_product(selected_name, source_path, active_name)" in source
    assert "_delete_selected_product(selected_name, source_path, active_name)" in source
    assert "activate_after_save=selected_is_active" in source
    assert "preserve_product=active_product if not selected_is_active else \"\"" in source
    assert "修改非当前产品时只刷新产品索引" in source
    assert "def _delete_current_product" not in source


def test_management_dialog_exposes_product_selector_and_separate_actions():
    source = Path("protocol_parser/product_manage_dialog.py").read_text(encoding="utf-8")
    assert "选择要修改或删除的产品 JSON" in source
    assert "self.product_combo = MatchedPopupComboBox" in source
    assert "修改所选产品" in source
    assert "删除所选产品" in source


def test_delete_marks_bundled_filename_before_protocol_refresh():
    source = Path("protocol_parser/mcu_page.py").read_text(encoding="utf-8")
    marker_call = "mark_product_json_deleted(source_path.name)"
    unlink_call = "source_path.unlink()"
    reload_call = "self._mw._load_protocols()"
    start = source.index("def _delete_selected_product")
    end = source.index("def _save_product_from_dialog", start)
    block = source[start:end]
    assert marker_call in block
    assert block.index(marker_call) < block.index(unlink_call) < block.index(reload_call)


def test_renamed_bundled_product_suppresses_old_filename_and_save_restores_new():
    source = Path("protocol_parser/mcu_page.py").read_text(encoding="utf-8")
    assert "clear_product_json_deleted(save_path.name)" in source
    assert "mark_product_json_deleted(old_source_path.name)" in source
