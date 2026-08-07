from __future__ import annotations

import json
from pathlib import Path

from protocol_parser.exceptions import (
    AttributeValidationError,
    ProductConfigError,
    SerialOperationError,
    SerialStateError,
    StorageOperationError,
)
from protocol_parser.parser import ProtocolConfigError
from protocol_parser.ui_error import (
    build_user_error_presentation,
    format_expected_user_error,
    is_expected_user_error,
    prompt_title_for_context,
)


ROOT = Path(__file__).resolve().parents[1]


def test_explicit_domain_errors_are_prompts() -> None:
    assert is_expected_user_error(AttributeValidationError("属性不能大于 15"))
    assert is_expected_user_error(ProductConfigError("缺少属性定义"))
    assert is_expected_user_error(SerialOperationError("串口不存在"))
    assert is_expected_user_error(SerialStateError("请先开始监控"))
    assert is_expected_user_error(StorageOperationError("磁盘空间不足"))
    assert is_expected_user_error(PermissionError("拒绝访问"))
    assert is_expected_user_error(ProtocolConfigError("缺少 product"))


def test_generic_programming_or_os_errors_are_not_hidden() -> None:
    # Generic built-ins may come from defects in application code.  They must
    # retain their traceback unless deliberately wrapped in a domain exception.
    assert not is_expected_user_error(ValueError("internal conversion defect"))
    assert not is_expected_user_error(OverflowError("internal overflow"))
    assert not is_expected_user_error(OSError("unexpected OS failure"))
    assert not is_expected_user_error(TypeError("bad call signature"))
    assert not is_expected_user_error(KeyError("missing"))
    assert not is_expected_user_error(IndexError("bad index"))
    assert not is_expected_user_error(AttributeError("missing member"))
    assert not is_expected_user_error(RuntimeError("internal invariant broken"))


def test_json_decode_message_contains_position() -> None:
    try:
        json.loads('{"a": 1,}')
    except json.JSONDecodeError as exc:
        text = format_expected_user_error(exc)
    else:  # pragma: no cover
        raise AssertionError("invalid JSON unexpectedly parsed")
    assert "JSON 格式不正确" in text
    assert "第 1 行" in text
    assert "请修改 JSON 后重试" in text


def test_context_titles_are_neutral_prompts() -> None:
    assert prompt_title_for_context("产品JSON导入失败") == "产品JSON内容不符合要求"
    assert prompt_title_for_context("属性发送失败") == "属性值不符合要求"
    assert prompt_title_for_context("自定义操作失败") == "自定义操作提示"


def test_expected_error_presentation_does_not_use_fault_wording() -> None:
    presentation = build_user_error_presentation(
        "产品JSON导入失败", ProductConfigError("功能定义中没有可导入的属性")
    )
    assert presentation is not None
    assert presentation.title == "产品JSON内容不符合要求"
    assert "功能定义中没有可导入的属性" in presentation.message
    assert "程序遇到未知错误" not in presentation.message
    assert "error.log" not in presentation.message


def test_all_user_input_entry_points_use_prompt_routing() -> None:
    gui_text = (ROOT / "protocol_parser" / "gui.py").read_text(encoding="utf-8")
    editor_text = (ROOT / "protocol_parser" / "attr_editor.py").read_text(encoding="utf-8")
    import_text = (ROOT / "protocol_parser" / "product_import_dialog.py").read_text(
        encoding="utf-8"
    )

    # The only red critical call is the final unexpected-fault branch.
    assert gui_text.count("QMessageBox.critical(") == 1
    assert 'self._report_error("指令库发送失败", e)' in gui_text
    assert "产品预设指令部分跳过" not in gui_text

    assert "QMessageBox.critical(" not in editor_text
    assert "0x00–0xFF" in editor_text
    assert "AttrStateCenter().load_product(candidate_cfg)" in editor_text
    assert 'access_combo.addItems(["读写", "只读", "只写"])' in editor_text

    assert "parse_function_json(self.json_text)" in import_text
    assert "功能JSON内容不符合要求" in import_text
