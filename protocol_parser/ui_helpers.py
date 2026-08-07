"""Shared UI formatting helpers for the two-page serial tool."""
from __future__ import annotations
import logging

from .exceptions import AttributeValidationError

import json
from datetime import datetime
from typing import Any

_log = logging.getLogger(__name__)


def _typeid_name(typeid: int) -> str:
    names = {
        0: "BOOL", 1: "INT8", 2: "UINT8", 3: "INT16", 4: "UINT16",
        5: "INT32", 6: "UINT32", 7: "INT64", 8: "UINT64",
        9: "FLOAT32", 10: "FLOAT64", 11: "STRING", 12: "DATE",
        13: "STRUCT", 14: "ARRAY", 15: "F1_U16", 16: "F2_U16",
        17: "F1_U32", 18: "F2_U32", 19: "F1_I16", 20: "F2_I16",
        21: "F1_I32", 22: "F2_I32", 23: "GROUP", 24: "STRING_ARRAY",
    }
    try:
        key = int(typeid)
    except (TypeError, ValueError):
        return f"UNK({typeid})"
    return names.get(key, f"UNK({key})")


def _convert_value(value_text: str, typeid: int) -> Any:
    text = str(value_text).strip()
    if typeid == 0:
        return text.lower() in ("1", "true", "on", "yes", "打开", "是")
    if typeid in (1, 2, 3, 4, 5, 6, 7, 8, 15, 16, 17, 18, 19, 20, 21, 22):
        if not text:
            raise AttributeValidationError("请输入属性值")
        return int(text, 0)
    if typeid in (9, 10):
        if not text:
            raise AttributeValidationError("请输入属性值")
        return float(text)
    if typeid in (13, 14, 23, 24):
        if not text:
            raise AttributeValidationError("请输入 JSON 值")
        return json.loads(text)
    return text


def format_attr_validation_message(
    entry: object,
    value_text: object,
    constraints: dict[str, Any] | None,
    error: BaseException | str,
) -> str:
    """Build a user-facing attribute validation prompt.

    This text is intentionally phrased as an input hint rather than a program
    error.  It is used by the MCU attribute controls when a value violates the
    product's enum/range/step/string-length constraints.
    """
    name = str(
        getattr(entry, "cn_name", "")
        or getattr(entry, "name", "")
        or "当前属性"
    )
    raw_value = str(value_text)
    shown_value = raw_value if len(raw_value) <= 80 else raw_value[:77] + "..."
    rules = constraints if isinstance(constraints, dict) else {}

    lines = [f"输入值“{shown_value}”不符合属性“{name}”的取值要求。"]

    enum_map = rules.get("enum")
    if isinstance(enum_map, dict) and enum_map:
        allowed = []
        for key, label in enum_map.items():
            key_text = str(key)
            label_text = str(label or "").strip()
            allowed.append(f"{key_text}（{label_text}）" if label_text else key_text)
        lines.append("允许值：" + "、".join(allowed))
    else:
        minimum = rules.get("minimum")
        maximum = rules.get("maximum")
        if minimum not in (None, "") and maximum not in (None, ""):
            lines.append(f"允许范围：{minimum}–{maximum}")
        elif minimum not in (None, ""):
            lines.append(f"最小值：{minimum}")
        elif maximum not in (None, ""):
            lines.append(f"最大值：{maximum}")

        step = rules.get("step")
        if step not in (None, "", 0, "0"):
            lines.append(f"取值步长：{step}")

        string_length = rules.get("string_length")
        if string_length not in (None, ""):
            lines.append(f"字符串最大长度：{string_length} 字节（UTF-8）")

    reason = str(error).strip()
    if reason:
        lines.append(f"提示：{reason}")
    lines.append("请修改后重新发送。")
    return "\n".join(lines)


def _parse_attrid(value: object) -> int | None:
    try:
        text = str(value).strip()
        return (int(text, 16) if text.lower().startswith("0x") else int(text)) & 0xFF
    except (TypeError, ValueError):
        return None


def _field_attr_candidates(field_obj: dict) -> list[dict]:
    """Return attribute records exposed by one public parser field."""
    candidates: list[dict] = []
    if "attrid" in field_obj:
        candidates.append(field_obj)
    children = field_obj.get("children")
    if isinstance(children, list):
        candidates.extend(
            child for child in children
            if isinstance(child, dict) and "attrid" in child
        )
    return candidates


def _enum_key_candidates(value: object) -> list[str]:
    keys: list[str] = []
    if isinstance(value, bool):
        keys.extend(["1" if value else "0", str(value)])
    elif isinstance(value, (int, float)):
        if isinstance(value, float) and value.is_integer():
            keys.append(str(int(value)))
        else:
            keys.append(str(value))
    else:
        text = str(value).strip()
        keys.append(text)
        try:
            numeric = float(text)
            if numeric.is_integer():
                keys.append(str(int(numeric)))
        except (TypeError, ValueError):
            pass
    return list(dict.fromkeys(keys))


def _format_attr_semantics(field_obj: dict, attr_center: object | None) -> str:
    """Format wire attributes with the current product's Chinese names/enums.

    MIOT products may use internal IDs such as 0x41/0x42 in the GUI while the
    wire protocol uses serialId 0/1/2... .  The binary parser can still decode
    the value, but it cannot resolve the business label from the wire ID alone.
    This display-only helper uses AttrStateCenter's canonical reverse mapping;
    it does not update values or affect automatic replies.
    """
    if attr_center is None:
        return ""

    parts: list[str] = []
    for candidate in _field_attr_candidates(field_obj):
        wire_id = _parse_attrid(candidate.get("attrid"))
        if wire_id is None:
            continue

        internal_id = None
        resolver = getattr(attr_center, "resolve_wire_attrid", None)
        if callable(resolver):
            try:
                internal_id = resolver(wire_id)
            except Exception:
                _log.debug("resolve_wire_attrid failed for wire_id=%s", wire_id, exc_info=True)
                internal_id = None
        if internal_id is None:
            internal_id = wire_id

        getter = getattr(attr_center, "get_entry", None)
        if not callable(getter):
            continue
        try:
            entry = getter(internal_id)
        except Exception:
            _log.debug("get_entry failed for internal_id=%s", internal_id, exc_info=True)
            entry = None
        if entry is None:
            continue

        # 展示层只做只读格式化，不调用 validate_attr_value，避免副作用/开销耦合。
        raw_value = candidate.get(
            "value_raw",
            candidate.get("value", field_obj.get("value")),
        )
        value = raw_value

        name = str(
            getattr(entry, "cn_name", "")
            or getattr(entry, "name", "")
            or f"属性0x{internal_id:02X}"
        )
        enum_map = getattr(entry, "enum", {}) or {}
        label = ""
        if isinstance(enum_map, dict):
            for key in _enum_key_candidates(value):
                if key in enum_map:
                    label = str(enum_map[key] or "").strip()
                    break

        try:
            entry_typeid = int(getattr(entry, "typeid", -1))
        except (TypeError, ValueError):
            entry_typeid = -1
        if not label and entry_typeid == 0:
            try:
                label = "开启" if bool(int(value)) else "关闭"
            except (TypeError, ValueError):
                label = "开启" if bool(value) else "关闭"

        shown = label or str(value)
        # 避免枚举文本本身已经包含属性名时重复显示，例如“照明照明开启”。
        semantic = shown if shown.startswith(name) else f"{name}{shown}"
        unit = str(getattr(entry, "unit", "") or "").strip()
        if unit and not label:
            semantic += f" {unit}"

        # 追加线协议字段（仅用于日志显示，不参与属性更新/自动回复）。
        # wire_id 由本字段原始 attrid 得出，正是 0x10/0x01 线上携带的 serialId。
        typeid_val = candidate.get("typeid")
        if typeid_val is None:
            typeid_val = getattr(entry, "typeid", None)
        data_val = candidate.get(
            "value_wire", candidate.get("value", candidate.get("value_raw", ""))
        )
        try:
            tid = int(typeid_val) if typeid_val is not None else -1
        except (TypeError, ValueError):
            tid = -1

        try:
            # Data 尽量按线协议形式显示：定长整数按其字节宽度补齐 HEX，
            # STRING/ARRAY 等仍保留短文本，便于直接和原始帧逐字节核对。
            if isinstance(data_val, (bytes, bytearray)):
                data_shown = bytes(data_val).hex().upper()
            elif isinstance(data_val, bool):
                data_shown = "01" if data_val else "00"
            elif isinstance(data_val, int) and tid >= 0:
                byte_widths = {
                    0: 1, 1: 1, 2: 1,
                    3: 2, 4: 2, 15: 2, 16: 2, 19: 2, 20: 2,
                    5: 4, 6: 4, 9: 4, 17: 4, 18: 4, 21: 4, 22: 4,
                    7: 8, 8: 8, 10: 8,
                }
                width = byte_widths.get(tid)
                if width is not None:
                    mask = (1 << (width * 8)) - 1
                    data_shown = f"{data_val & mask:0{width * 2}X}"
                else:
                    data_shown = str(data_val)
            else:
                data_shown = str(data_val)
        except Exception:
            data_shown = str(data_val)

        tid_shown = f"{tid & 0xFF:02X}" if tid >= 0 else "??"
        semantic += f" Typeid:{tid_shown} Attrid:{wire_id:02X} Data:{data_shown}"
        parts.append(semantic)

    return "，".join(parts)


def _recover_display_attr_fields(
    raw: bytes | bytearray | None,
    cmd_code: str,
    attr_center: object | None,
) -> list[dict]:
    """Recover 0x01/0x10 attribute fields for display only.

    Some imported products define 0x01 as only a message-id field.  Automatic
    reply already recovers the hidden attribute payload, but the live display
    should also work when auto reply is disabled.  This helper decodes the
    synchronized frame without mutating the parser result or attribute state.
    """
    if attr_center is None or str(cmd_code).lower() not in ("0x01", "0x10"):
        return []
    frame = bytes(raw or b"")
    if len(frame) < 7:
        return []
    try:
        data_len = int.from_bytes(frame[4:6], "big")
        if data_len < 0 or 6 + data_len > len(frame):
            return []
        data = frame[6:6 + data_len]
        if str(cmd_code).lower() == "0x01":
            if not data:
                return []
            data = data[1:]  # leading message id
        if not data:
            return []
        from .parser import parse_attr_payload_fields

        return parse_attr_payload_fields(
            data,
            getattr(attr_center, "cfg", {}) or {},
            force_report=str(cmd_code).lower() == "0x10",
        )
    except Exception:
        return []


def _format_fields_summary(
    result: object | None,
    *,
    raw: bytes | bytearray | None = None,
    attr_center: object | None = None,
) -> str:
    """Build a compact, product-aware field summary from ParseResult.fields."""
    if result is None:
        return ""
    fields = list(getattr(result, "fields", None) or [])
    cmd_code = str(getattr(result, "cmd_code", "") or "")

    has_attr_fields = any(
        isinstance(field_obj, dict) and bool(_field_attr_candidates(field_obj))
        for field_obj in fields
    )
    if not has_attr_fields:
        fields.extend(_recover_display_attr_fields(raw, cmd_code, attr_center))

    summaries: list[str] = []
    in_data = False
    for field_obj in fields:
        if not isinstance(field_obj, dict):
            continue
        field_type = str(field_obj.get("type") or "")
        if field_type == "separator":
            in_data = True
            continue
        if not in_data and field_type in ("header", "version", "cmd", "length", "checksum"):
            continue

        attr_semantic = _format_attr_semantics(field_obj, attr_center)
        if attr_semantic:
            summaries.append(attr_semantic)
            continue

        name = str(
            field_obj.get("cn_name")
            or field_obj.get("name")
            or field_obj.get("label")
            or "字段"
        )
        if name.startswith("attrid_"):
            continue
        text = field_obj.get("text")
        if text in (None, ""):
            text = field_obj.get("value")
        if text in (None, ""):
            text = field_obj.get("value_raw")
        if text in (None, ""):
            children = field_obj.get("children")
            if isinstance(children, list):
                child_parts: list[str] = []
                for child in children:
                    if not isinstance(child, dict):
                        continue
                    child_name = str(child.get("cn_name") or child.get("name") or child.get("attrid") or "属性")
                    child_value = child.get("text", child.get("value", child.get("value_raw", "")))
                    if child_value not in (None, ""):
                        child_parts.append(f"{child_name}:{child_value}")
                if child_parts:
                    summaries.append("，".join(child_parts))
            continue
        summaries.append(f"{name}:{text}")
    return "；".join(summaries)


_MCU_TX_COMMAND_NAMES = {
    "0x01": "命令回复",
    "0x03": "回复设备属性值",
    "0x10": "状态上报",
    "0x11": "设备事件上报",
    "0x20": "回复心跳",
    "0x21": "回复设备信息",
    "0x23": "发起配网",
    "0x24": "回复设备快照",
    "0x25": "报告MCU工作状态",
    "0x26": "请求时间",
}


def format_frame_display(
    result: object | None,
    raw: bytes | bytearray | None,
    ts: float,
    *,
    is_tx: bool = False,
    auto_reply: bool = False,
    attr_center: object | None = None,
) -> str:
    """Format one MCU-page frame as two readable lines.

    The first line always carries timestamp, RX/TX and raw HEX.  The second line
    adds command name, direction and decoded fields when parsing succeeded.
    """
    timestamp = datetime.fromtimestamp(float(ts)).strftime("%H:%M:%S.%f")[:-3]
    raw_bytes = bytes(raw or b"")
    raw_hex = raw_bytes.hex(" ").upper()
    direction_tag = "TX" if is_tx else "RX"
    lines = [f"[{timestamp}] [{direction_tag}] {raw_hex}".rstrip()]

    if result is not None:
        cmd_code = str(getattr(result, "cmd_code", "") or "")
        cmd_name = str(getattr(result, "cmd_name", "") or "未知命令")
        if is_tx:
            cmd_name = _MCU_TX_COMMAND_NAMES.get(cmd_code.lower(), cmd_name)
        # 模拟 MCU 页面中，方向由数据实际流向决定：
        # 本工具发出的所有帧（属性上报、预置命令、自动回复）均为 MCU→模组；
        # 串口接收到的帧均为 模组→MCU。不要沿用协议定义中的静态 direction。
        direction = "MCU→模组" if is_tx else "模组→MCU"
        details = _format_fields_summary(result, raw=raw_bytes, attr_center=attr_center)
        semantic = f"  → {cmd_name}"
        if cmd_code:
            semantic += f" ({cmd_code})"
        if direction:
            semantic += f" | {direction}"
        if details:
            semantic += f" | {details}"
        if auto_reply:
            semantic += " (自动回复)"
        lines.append(semantic)
    elif auto_reply:
        lines.append("  → 自动回复")

    return "\n".join(lines) + "\n"
