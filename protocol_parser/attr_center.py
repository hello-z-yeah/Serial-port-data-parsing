"""实时属性状态中心。"""
from __future__ import annotations
from .exceptions import AttributeValidationError

import ast
import copy
from dataclasses import dataclass, field, replace
import json
import math
from threading import RLock
from typing import Any

from .product_importer import extract_enum_map, extract_range_value
from .dev_info_encoder import build_snapshot_attrid_map


_UNSET = object()


TYPEID_DEFAULTS = {
    0: False,
    1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 7: 0, 8: 0,
    9: 0.0, 10: 0.0,
    11: "", 12: "", 13: {}, 14: [],
    15: 0, 16: 0, 17: 0, 18: 0, 19: 0, 20: 0, 21: 0, 22: 0,
    23: [], 24: [],
}


INTEGER_TYPE_BOUNDS = {
    1: (-128, 127),
    2: (0, 255),
    3: (-32768, 32767),
    4: (0, 65535),
    5: (-2147483648, 2147483647),
    6: (0, 4294967295),
    7: (-9223372036854775808, 9223372036854775807),
    8: (0, 18446744073709551615),
    15: (0, 65535),
    16: (0, 65535),
    17: (0, 4294967295),
    18: (0, 4294967295),
    19: (-32768, 32767),
    20: (-32768, 32767),
    21: (-2147483648, 2147483647),
    22: (-2147483648, 2147483647),
}


@dataclass
class AttrEntry:
    attrid: int
    name: str
    cn_name: str
    typeid: int
    access: str
    original_name: str = ""
    source_attribute_key: str = ""
    source_attribute_name: str = ""
    unit: str = ""
    enum: dict = field(default_factory=dict)
    range_str: str = ""
    current_value: Any = 0
    batch_value: Any = None


class AttrStateCenter:
    @staticmethod
    def _enum_map(meta: dict) -> dict[str, str]:
        return extract_enum_map(meta if isinstance(meta, dict) else {})

    @classmethod
    def _source_attr_metadata(cls, cfg: dict) -> dict[int, dict]:
        """Recover original English labels/enums from saved source JSON.

        Older imported products may not have persisted ``original_name`` or
        ``enum`` in the normalized attributes, while ``source_function_json``
        still contains them. This only supplements display metadata.
        """
        raw = (cfg or {}).get("source_function_json")
        if raw in (None, ""):
            return {}
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            return {}

        result: dict[int, dict] = {}
        if isinstance(data, dict) and isinstance(data.get("services"), list):
            used_ids: set[int] = set()
            for service in data.get("services") or []:
                if not isinstance(service, dict):
                    continue
                try:
                    siid = int(service.get("iid", service.get("siid", 0)) or 0)
                except (TypeError, ValueError):
                    siid = 0
                service_name = str(service.get("description") or service.get("name") or "")
                for prop in service.get("properties", []) or []:
                    if not isinstance(prop, dict):
                        continue
                    try:
                        piid = int(prop.get("iid", prop.get("piid", 0)) or 0)
                    except (TypeError, ValueError):
                        continue
                    attrid = ((siid * 16 + piid) + 0x20) & 0xFF
                    while attrid in used_ids and attrid < 0xFF:
                        attrid += 1
                    used_ids.add(attrid)
                    prop_name = str(prop.get("description") or prop.get("name") or piid)
                    result[attrid] = {
                        "original_name": (
                            f"{service_name}-{prop_name}" if service_name else prop_name
                        ),
                        "enum": cls._enum_map(prop),
                        "range": extract_range_value(prop),
                    }
        elif isinstance(data, dict) and isinstance(data.get("Attrs"), list):
            for item in data.get("Attrs") or []:
                if not isinstance(item, dict):
                    continue
                raw_id = item.get("serialId", item.get("attrid", item.get("id")))
                try:
                    attrid = int(str(raw_id), 16) if str(raw_id).lower().startswith("0x") else int(raw_id)
                except (TypeError, ValueError):
                    continue

                enum_map: dict[str, str] = {}
                range_value: Any = ""
                raw_data_value = item.get("dataValue")
                if isinstance(raw_data_value, str) and raw_data_value.strip():
                    try:
                        parsed_data_value = json.loads(raw_data_value)
                    except Exception:
                        parsed_data_value = None
                    if isinstance(parsed_data_value, dict):
                        if "min" in parsed_data_value or "max" in parsed_data_value:
                            range_value = parsed_data_value
                        elif "length" in parsed_data_value:
                            range_value = parsed_data_value
                        else:
                            enum_map = {str(k): str(v) for k, v in parsed_data_value.items()}

                rwx = str(item.get("dataRwx", "r") or "r").strip().lower()
                attribute_key = str(item.get("attributeKey") or "").strip()
                attribute_name = str(item.get("attributeName") or "").strip()
                result[attrid] = {
                    "original_name": str(
                        attribute_name
                        or attribute_key
                        or item.get("attributeDesc")
                        or attrid
                    ),
                    "source_attribute_key": attribute_key,
                    "source_attribute_name": attribute_name,
                    "enum": enum_map,
                    "range": range_value,
                    "initial_value": item.get("nowValue"),
                    "snapshot_wire_id": attrid & 0xFF,
                    "snapshot_include": "r" in rwx,
                }
        elif isinstance(data, dict):
            for raw_id, meta in data.items():
                if str(raw_id).startswith("__") or not isinstance(meta, dict):
                    continue
                try:
                    attrid = int(str(raw_id), 16) if str(raw_id).lower().startswith("0x") else int(raw_id)
                except (TypeError, ValueError):
                    continue
                result[attrid] = {
                    "original_name": str(
                        meta.get("original_name")
                        or meta.get("description")
                        or meta.get("display_name")
                        or meta.get("cn_name")
                        or meta.get("name")
                        or ""
                    ),
                    "enum": cls._enum_map(meta),
                    "range": extract_range_value(meta),
                }
        elif isinstance(data, list):
            for meta in data:
                if not isinstance(meta, dict):
                    continue
                raw_id = meta.get("attrid", meta.get("id", meta.get("attr_id")))
                try:
                    attrid = int(str(raw_id), 16) if str(raw_id).lower().startswith("0x") else int(raw_id)
                except (TypeError, ValueError):
                    continue
                result[attrid] = {
                    "original_name": str(
                        meta.get("original_name")
                        or meta.get("description")
                        or meta.get("display_name")
                        or meta.get("name")
                        or ""
                    ),
                    "enum": cls._enum_map(meta),
                    "range": extract_range_value(meta),
                }
        return result

    def __init__(self) -> None:
        self.cfg: dict = {}
        self._attrs: dict[int, AttrEntry] = {}
        self._attr_order: list[int] = []
        self._wire_to_internal: dict[int, int] = {}
        self._lock = RLock()
        self._heartbeat_count = 0
        self._generation = 0
        self.load_warnings: list[str] = []

    def load_product(self, cfg: dict) -> None:
        with self._lock:
            old_state = (
                self.cfg, self._attrs, self._attr_order,
                self._wire_to_internal, self.load_warnings, self._heartbeat_count,
            )
            try:
                target_cfg = cfg or {}
                warnings: list[str] = []
                source_meta = self._source_attr_metadata(target_cfg)
                self._attrs = {}
                self._attr_order = []
                self._wire_to_internal = {}
                for raw_id, meta in (target_cfg.get("attributes") or {}).items():
                    if str(raw_id).startswith("__") or not isinstance(meta, dict):
                        continue
                    try:
                        attrid = int(str(raw_id), 16) if str(raw_id).lower().startswith("0x") else int(raw_id)
                        typeid = int(meta.get("typeid", 2))
                    except (TypeError, ValueError):
                        warnings.append(f"属性 {raw_id!r} 的 attrid/typeid 非法，已跳过")
                        continue
                    if not 0 <= attrid <= 0xFF:
                        warnings.append(f"属性 {raw_id!r} 超出 0..255，已跳过")
                        continue
                    if typeid not in TYPEID_DEFAULTS:
                        warnings.append(
                            f"属性 0x{attrid:02X} 使用不支持的 typeid={typeid}，已跳过"
                        )
                        continue
                    source = source_meta.get(attrid) or {}
                    range_value = meta.get("range")
                    if range_value in (None, ""):
                        range_value = source.get("range", "")
                    if isinstance(range_value, (list, tuple)) and len(range_value) >= 2:
                        step_value = range_value[2] if len(range_value) >= 3 else meta.get("step")
                        if step_value not in (None, ""):
                            range_value = f"[{range_value[0]},{range_value[1]},{step_value}]"
                        else:
                            range_value = f"[{range_value[0]},{range_value[1]}]"
                    elif meta.get("step") not in (None, "") and range_value not in (None, ""):
                        # Preserve a separately normalized step value when the
                        # imported range was stored as a string/dict.
                        parsed_range = self._parse_constraint(range_value)
                        if isinstance(parsed_range, dict):
                            parsed_range = dict(parsed_range)
                            parsed_range.setdefault("step", meta.get("step"))
                            range_value = json.dumps(parsed_range, ensure_ascii=False)
                        elif isinstance(parsed_range, (list, tuple)) and len(parsed_range) >= 2:
                            range_value = (
                                f"[{parsed_range[0]},{parsed_range[1]},{meta.get('step')}]"
                            )
                    default_value = copy.deepcopy(TYPEID_DEFAULTS.get(typeid, 0))
                    initial_value = meta.get("initial_value", source.get("initial_value", default_value))
                    if initial_value is None:
                        initial_value = copy.deepcopy(default_value)
                    initial_value = self._coerce_value(initial_value, typeid)

                    entry = AttrEntry(
                        attrid=attrid,
                        name=str(meta.get("name") or ""),
                        cn_name=str(meta.get("cn_name") or meta.get("name") or ""),
                        typeid=typeid,
                        access=str(meta.get("access") or "读写"),
                        original_name=str(
                            meta.get("original_name")
                            or source.get("original_name")
                            or meta.get("description")
                            or meta.get("name")
                            or ""
                        ),
                        source_attribute_key=str(
                            meta.get("source_attribute_key")
                            or source.get("source_attribute_key")
                            or ""
                        ),
                        source_attribute_name=str(
                            meta.get("source_attribute_name")
                            or source.get("source_attribute_name")
                            or ""
                        ),
                        unit=str(meta.get("unit") or ""),
                        enum=dict(meta.get("enum") or source.get("enum") or {}),
                        range_str=str(range_value or ""),
                        current_value=initial_value,
                    )
                    self._attrs[attrid] = entry
                    self._attr_order.append(attrid)
                    wire_raw = meta.get("snapshot_wire_id", source.get("snapshot_wire_id", attrid))
                    try:
                        wire_attrid = int(str(wire_raw), 16) if str(wire_raw).lower().startswith("0x") else int(wire_raw)
                        self._wire_to_internal[wire_attrid & 0xFF] = attrid
                    except (TypeError, ValueError):
                        self._wire_to_internal[attrid & 0xFF] = attrid

                # 0x21 设备信息、0x24 快照、0x10 状态上报和 0x01 命令下发
                # 必须共享同一套“内部属性 ID <-> 线协议 serialId”映射。
                #
                # MIOT services/properties 产品在界面中使用 0x41/0x42 等内部 ID，
                # 但 0x21 对模组公布的是按属性顺序生成的 0、1、2... serialId。
                # 旧实现这里只读取 snapshot_wire_id；MIOT 产品没有显式字段时，
                # 收到 serialId=3 的 0x01 命令会被误判为未知属性，因此心跳能
                # 自动回复，而带消息 ID 的属性命令完全不回复。
                #
                # build_snapshot_attrid_map() 正是 0x21/0x24/0x10 编码使用的唯一
                # 映射源。映射处于活动状态时，接收方向也必须使用其反向映射，
                # 避免收发两套规则漂移。
                canonical_map, wire_mapping_active = build_snapshot_attrid_map(target_cfg)
                if wire_mapping_active and canonical_map:
                    reverse_map: dict[int, int] = {}
                    ambiguous_wire_ids: set[int] = set()
                    for internal_attrid, wire_attrid in canonical_map.items():
                        internal = int(internal_attrid) & 0xFF
                        wire = int(wire_attrid) & 0xFF
                        if internal not in self._attrs:
                            continue
                        previous = reverse_map.get(wire)
                        if previous is not None and previous != internal:
                            ambiguous_wire_ids.add(wire)
                            continue
                        reverse_map[wire] = internal
                    # 重复 serialId 不能随意映射到其中一个属性；保持未映射，
                    # 让自动回复拒绝该命令并给出明确提示，避免修改错误属性。
                    for wire in ambiguous_wire_ids:
                        reverse_map.pop(wire, None)
                    self._wire_to_internal = reverse_map

                # 导入产品时，nowValue/initial_value 可能为空、越界，或者不在枚举
                # 集合中。不能把这种非法初始值继续带入快照、状态上报和自动生成
                # 指令；统一回退到该属性的第一个合法业务值。
                for attrid in self._attr_order:
                    entry = self._attrs[attrid]
                    entry.current_value = self._resolve_valid_value_locked(
                        entry, entry.current_value
                    )
                # 最后一次性发布 cfg，避免无锁读取者看到“新 cfg + 旧属性表”。
                self.cfg = target_cfg
                self.load_warnings = warnings
                self._heartbeat_count = 0
                # 产品/协议切换代数：异步回调必须携带捕获时的 generation；
                # 若小于当前值则视为过期帧，丢弃写入，防止旧协议 ID 污染新状态。
                self._generation += 1

            except Exception:
                (
                    self.cfg, self._attrs, self._attr_order,
                    self._wire_to_internal, self.load_warnings, self._heartbeat_count,
                ) = old_state
                raise

    @staticmethod
    def _cmd_int(result) -> int:
        raw = getattr(result, "cmd_code", 0)
        if isinstance(raw, int):
            return raw & 0xFF
        try:
            return int(str(raw), 16) if str(raw).lower().startswith("0x") else int(raw)
        except (TypeError, ValueError):
            raw_hex = str(getattr(result, "raw_hex", "") or "").replace(" ", "")
            try:
                return int(raw_hex[6:8], 16) if len(raw_hex) >= 8 else 0
            except (TypeError, ValueError, AttributeValidationError):
                return 0

    @staticmethod
    def _coerce_value(value: Any, typeid: int) -> Any:
        if value is None:
            return None
        if typeid == 0:
            if isinstance(value, str):
                return value.strip().lower() in ("1", "true", "on", "yes", "打开")
            return bool(value)
        if typeid in (1, 2, 3, 4, 5, 6, 7, 8, 15, 16, 17, 18, 19, 20, 21, 22):
            if isinstance(value, float) and not value.is_integer():
                return value
            try:
                if isinstance(value, str):
                    text = value.strip()
                    return int(text, 0) if text.lower().startswith(("0x", "0o", "0b")) else int(text)
                return int(value)
            except (TypeError, ValueError):
                return value
        if typeid in (9, 10):
            try:
                return float(value)
            except (TypeError, ValueError):
                return value
        return value

    @staticmethod
    def _parse_constraint(raw: Any) -> Any:
        if raw in (None, ""):
            return None
        if isinstance(raw, (dict, list, tuple)):
            return raw
        text = str(raw).strip()
        if not text:
            return None
        for parser in (json.loads, ast.literal_eval):
            try:
                return parser(text)
            except Exception:
                continue
        return text

    @classmethod
    def _range_parts(cls, entry: AttrEntry) -> tuple[Any, Any, Any, int | None]:
        """Return (minimum, maximum, step, string_length)."""
        parsed = cls._parse_constraint(entry.range_str)
        minimum = maximum = step = None
        string_length: int | None = None
        if isinstance(parsed, dict):
            minimum = parsed.get("min", parsed.get("minimum"))
            maximum = parsed.get("max", parsed.get("maximum"))
            step = parsed.get("step")
            length = parsed.get("length", parsed.get("maxLength"))
            if length not in (None, ""):
                try:
                    string_length = max(0, int(length))
                except (TypeError, ValueError):
                    pass
        elif isinstance(parsed, (list, tuple)):
            if len(parsed) >= 2:
                minimum, maximum = parsed[0], parsed[1]
            if len(parsed) >= 3:
                step = parsed[2]
        return minimum, maximum, step, string_length

    @staticmethod
    def _enum_sort_key(item: tuple[Any, Any]) -> tuple[int, Any]:
        raw = str(item[0]).strip()
        try:
            return 0, int(raw, 0)
        except (TypeError, ValueError):
            try:
                return 0, float(raw)
            except (TypeError, ValueError):
                return 1, raw.lower()

    def _resolve_valid_value_locked(
        self,
        entry: AttrEntry,
        preferred: Any = _UNSET,
    ) -> Any:
        """Return a legal value for an attribute without inventing ``0`` blindly.

        Candidate order is intentional: explicit/current value first, then a
        legal enum member, then a range-compatible type default.  If the
        metadata itself is impossible (for example a BOOL enum containing only
        ``2``), a precise ``ValueError`` is raised so the product definition can
        be corrected instead of silently generating invalid frames.
        """
        candidates: list[Any] = []
        if preferred is not _UNSET:
            candidates.append(preferred)

        # 枚举产品最容易出现“允许 1/2，但通用默认值是 0”的情况，枚举值必须
        # 优先于通用类型默认值。
        if entry.enum:
            candidates.extend(
                raw for raw, _ in sorted(entry.enum.items(), key=self._enum_sort_key)
            )

        minimum, maximum, _step, _string_length = self._range_parts(entry)
        type_default = TYPEID_DEFAULTS.get(entry.typeid, 0)

        if entry.typeid in INTEGER_TYPE_BOUNDS:
            candidate = type_default
            try:
                numeric = int(candidate)
                if minimum not in (None, ""):
                    numeric = max(numeric, math.ceil(float(minimum)))
                if maximum not in (None, ""):
                    numeric = min(numeric, math.floor(float(maximum)))
                candidates.append(numeric)
            except (TypeError, ValueError, OverflowError):
                pass
            if minimum not in (None, ""):
                try:
                    candidates.append(math.ceil(float(minimum)))
                except (TypeError, ValueError, OverflowError):
                    pass
            if maximum not in (None, ""):
                try:
                    candidates.append(math.floor(float(maximum)))
                except (TypeError, ValueError, OverflowError):
                    pass
        elif entry.typeid in (9, 10):
            candidate = float(type_default)
            try:
                if minimum not in (None, ""):
                    candidate = max(candidate, float(minimum))
                if maximum not in (None, ""):
                    candidate = min(candidate, float(maximum))
                candidates.append(candidate)
            except (TypeError, ValueError, OverflowError):
                pass
            if minimum not in (None, ""):
                candidates.append(minimum)
            if maximum not in (None, ""):
                candidates.append(maximum)
        else:
            candidates.append(type_default)

        failures: list[str] = []
        for candidate in candidates:
            try:
                return self.validate_attr_value(entry.attrid, candidate)
            except ValueError as exc:
                failures.append(str(exc))

        name = entry.cn_name or entry.name or f"0x{entry.attrid:02X}"
        reason = failures[-1] if failures else "没有可用默认值"
        raise AttributeValidationError(f"属性“{name}”无法确定合法初始值：{reason}")

    def get_valid_default_value(self, attrid: int, preferred: Any = _UNSET) -> Any:
        """Return a validated default/current value suitable for frame encoding."""
        with self._lock:
            entry = self._attrs.get(int(attrid))
            if entry is None:
                raise AttributeValidationError(f"未知属性 0x{int(attrid) & 0xFF:02X}")
            resolved_preferred = entry.current_value if preferred is _UNSET else preferred
            return self._resolve_valid_value_locked(entry, resolved_preferred)

    def validate_attr_value(self, attrid: int, value: Any) -> Any:
        """Coerce and validate a value against type/enum/range metadata.

        The returned value is the normalized value that should be encoded and
        stored.  Invalid UI input raises ``ValueError`` instead of silently
        producing a protocol-valid but product-invalid frame.
        """
        with self._lock:
            entry = self._attrs.get(int(attrid))
            if entry is None:
                raise AttributeValidationError(f"未知属性 0x{int(attrid) & 0xFF:02X}")

            normalized = self._coerce_value(value, entry.typeid)
            if normalized is None:
                raise AttributeValidationError(f"属性“{entry.cn_name or entry.name}”的值不能为空")

            if entry.typeid in INTEGER_TYPE_BOUNDS and not isinstance(normalized, int):
                raise AttributeValidationError(f"属性“{entry.cn_name or entry.name}”需要整数")
            if entry.typeid in (9, 10) and not isinstance(normalized, (int, float)):
                raise AttributeValidationError(f"属性“{entry.cn_name or entry.name}”需要数值")

            if entry.enum:
                enum_keys = {str(key).strip() for key in entry.enum.keys()}
                candidate = str(int(normalized) if isinstance(normalized, bool) else normalized).strip()
                if candidate not in enum_keys:
                    raise AttributeValidationError(
                        f"属性“{entry.cn_name or entry.name}”仅允许："
                        + "、".join(sorted(enum_keys))
                    )

            if entry.typeid in INTEGER_TYPE_BOUNDS:
                low, high = INTEGER_TYPE_BOUNDS[entry.typeid]
                if not low <= int(normalized) <= high:
                    raise AttributeValidationError(
                        f"属性“{entry.cn_name or entry.name}”超出类型范围 {low}–{high}"
                    )

            minimum, maximum, step, string_length = self._range_parts(entry)
            if isinstance(normalized, (int, float)) and not isinstance(normalized, bool):
                if minimum not in (None, ""):
                    try:
                        if normalized < float(minimum):
                            raise AttributeValidationError(
                                f"属性“{entry.cn_name or entry.name}”不能小于 {minimum}"
                            )
                    except (TypeError, ValueError) as exc:
                        if isinstance(exc, ValueError) and "不能小于" in str(exc):
                            raise
                if maximum not in (None, ""):
                    try:
                        if normalized > float(maximum):
                            raise AttributeValidationError(
                                f"属性“{entry.cn_name or entry.name}”不能大于 {maximum}"
                            )
                    except (TypeError, ValueError) as exc:
                        if isinstance(exc, ValueError) and "不能大于" in str(exc):
                            raise
                if step not in (None, "", 0, "0") and minimum not in (None, ""):
                    try:
                        quotient = (float(normalized) - float(minimum)) / float(step)
                        if not math.isclose(quotient, round(quotient), abs_tol=1e-9):
                            raise AttributeValidationError(
                                f"属性“{entry.cn_name or entry.name}”必须按步长 {step} 取值"
                            )
                    except (TypeError, ValueError) as exc:
                        if isinstance(exc, ValueError) and "必须按步长" in str(exc):
                            raise

            if entry.typeid in (11, 12):
                normalized = str(normalized)
                if string_length is not None and len(normalized.encode("utf-8")) > string_length:
                    raise AttributeValidationError(
                        f"属性“{entry.cn_name or entry.name}”UTF-8长度不能超过 {string_length} 字节"
                    )

            if entry.typeid in (13, 14, 23, 24) and isinstance(normalized, str):
                try:
                    normalized = json.loads(normalized)
                except Exception as exc:
                    raise AttributeValidationError(
                        f"属性“{entry.cn_name or entry.name}”需要合法 JSON"
                    ) from exc
            return normalized

    def get_value_constraints(self, attrid: int) -> dict[str, Any]:
        with self._lock:
            entry = self._attrs.get(int(attrid))
            if entry is None:
                return {}
            minimum, maximum, step, string_length = self._range_parts(entry)
            return {
                "minimum": minimum,
                "maximum": maximum,
                "step": step,
                "string_length": string_length,
                "enum": dict(entry.enum),
            }

    def _frame_attr_records(self, result) -> list[tuple[int, int | None, Any]]:
        """Return (wire_id, internal_id, raw_value) records from a frame."""
        records: list[tuple[int, int | None, Any]] = []
        fields = getattr(result, "fields", None) or []
        with self._lock:
            for field_obj in fields:
                if not isinstance(field_obj, dict):
                    continue
                candidates: list[dict] = []
                if "attrid" in field_obj:
                    candidates.append(field_obj)
                elif str(field_obj.get("type") or "").lower() == "attrid":
                    # msg_id_then_attr_unit (0x03 request) exposes each queried
                    # id as an attrid-typed field rather than an attr-list child.
                    candidates.append({"attrid": field_obj.get("value"), "value": None})
                children = field_obj.get("children") or []
                if isinstance(children, list):
                    candidates.extend(
                        child for child in children
                        if isinstance(child, dict) and "attrid" in child
                    )
                for child in candidates:
                    raw_id = child.get("attrid")
                    try:
                        wire_id = (
                            int(str(raw_id), 16)
                            if str(raw_id).lower().startswith("0x")
                            else int(raw_id)
                        ) & 0xFF
                    except (TypeError, ValueError):
                        continue
                    internal_id = self._wire_to_internal.get(wire_id)
                    if internal_id is None and wire_id in self._attrs:
                        internal_id = wire_id
                    typeid_raw = child.get("typeid")
                    try:
                        typeid = int(typeid_raw) if typeid_raw is not None else None
                    except (TypeError, ValueError):
                        typeid = None
                    if typeid in range(15, 23) and "value_wire" in child:
                        value = child.get("value_wire")
                    elif "value" in child:
                        value = child.get("value")
                    else:
                        value = child.get("value_raw", field_obj.get("value"))
                    records.append((wire_id, internal_id, value))
        return records

    def resolve_wire_attrid(self, wire_attrid: int) -> int | None:
        """Resolve a line-protocol serialId to the GUI/internal attribute ID.

        The map is built from the same canonical source used by 0x21/0x24/0x10
        encoding, so incoming 0x01/0x03 frames and outgoing reports cannot drift
        to different attribute-numbering schemes.
        """
        with self._lock:
            return self._wire_to_internal.get(int(wire_attrid) & 0xFF)

    def get_frame_attr_records(self, result) -> list[tuple[int, int | None, Any]]:
        """Public read-only view of frame attribute records.

        Each tuple is ``(wire_id, internal_id_or_none, raw_value)``.  Automatic
        reply logic uses this to validate an entire command before acknowledging
        it, so unknown or business-invalid values cannot be reported as success.
        """
        return list(self._frame_attr_records(result))

    def get_frame_attrids(self, result) -> list[int]:
        """返回帧中实际携带的内部属性 ID（无论值是否发生变化）。

        模组命令下发后，MCU需要先回复消息 ID，再针对命令中携带的属性
        发送一次状态上报。这里不能只依赖“值是否变化”，因为同值重发、
        或其他显示通道已提前更新属性中心时，变化列表可能为空。
        """
        attrids: list[int] = []
        for _, internal_id, _ in self._frame_attr_records(result):
            if internal_id is not None and internal_id not in attrids:
                attrids.append(internal_id)
        return attrids

    def get_unknown_frame_attrids(self, result) -> list[int]:
        unknown: list[int] = []
        for wire_id, internal_id, _ in self._frame_attr_records(result):
            if internal_id is None and wire_id not in unknown:
                unknown.append(wire_id)
        return unknown

    def update_from_frame(self, result, *, expected_generation: int | None = None) -> list[int]:
        """从 ParseResult 更新属性，返回发生变化的 attrid。

        expected_generation: 若由异步回调传入且与当前 generation 不一致，则整帧丢弃，
        防止跨产品切换时的幽灵回调把旧协议 Attribute ID 写入新状态中心。
        """
        if self._cmd_int(result) not in (0x10, 0x01, 0x24, 0x03):
            return []
        changed: list[int] = []
        cmd_int = self._cmd_int(result)
        with self._lock:
            if expected_generation is not None and expected_generation != self._generation:
                return []
            for _, internal_id, raw_value in self._frame_attr_records(result):
                entry = self._attrs.get(internal_id) if internal_id is not None else None
                if entry is None:
                    continue
                # 0x01 是模组下发写命令。只读属性不能被远端命令篡改；
                # 0x10/0x24/0x03 则是状态/快照/查询结果，可更新只读属性。
                if cmd_int == 0x01 and entry.access not in ("读写", "只写"):
                    continue
                if raw_value is None:
                    continue
                try:
                    value = self.validate_attr_value(entry.attrid, raw_value)
                except AttributeValidationError:
                    # 接收到的字节可能在协议类型上合法，但超出当前产品的枚举、
                    # 范围、步长或字符串长度。此时不污染实时属性中心。
                    continue
                if value != entry.current_value:
                    entry.current_value = value
                    if entry.attrid not in changed:
                        changed.append(entry.attrid)
        return changed

    def get_entry(self, attrid: int) -> AttrEntry | None:
        with self._lock:
            entry = self._attrs.get(int(attrid))
            return (
                replace(
                    entry,
                    enum=copy.deepcopy(entry.enum),
                    current_value=copy.deepcopy(entry.current_value),
                    batch_value=copy.deepcopy(entry.batch_value),
                )
                if entry is not None
                else None
            )

    def get_attr_values(self, attrids: list[int] | None = None) -> list[tuple[int, Any, int]]:
        with self._lock:
            targets = list(attrids) if attrids is not None else self.get_readable_attrs()
            return [
                (entry.attrid, copy.deepcopy(entry.current_value), entry.typeid)
                for aid in targets
                if (entry := self._attrs.get(aid)) is not None
            ]

    def get_attr_value(self, attrid: int) -> tuple[int, Any, int]:
        with self._lock:
            entry = self._attrs.get(int(attrid))
            if entry is None:
                return int(attrid), 0, 2
            return entry.attrid, copy.deepcopy(entry.current_value), entry.typeid

    def set_attr_value(self, attrid: int, value: Any, *, expected_generation: int | None = None) -> None:
        with self._lock:
            if expected_generation is not None and expected_generation != self._generation:
                return
            entry = self._attrs.get(int(attrid))
            if entry is not None:
                entry.current_value = self.validate_attr_value(attrid, value)

    def apply_values_atomic(
        self,
        values: dict[int, Any],
        *,
        expected_generation: int | None = None,
    ) -> dict[int, Any]:
        """在同一把锁下校验并写入多个属性，保证无中间态可见。

        返回被修改属性的旧值快照，供调用方在后续失败时调用
        :meth:`restore_values` 完整回滚。任一属性校验失败时，**不会**
        修改任何属性，异常直接抛出。

        expected_generation: 若由异步路径传入且与当前 generation 不一致，则丢弃写入并返回空快照。
        """
        if not values:
            return {}
        with self._lock:
            if expected_generation is not None and expected_generation != self._generation:
                return {}
            normalized: dict[int, Any] = {}
            for attrid, value in values.items():
                aid = int(attrid)
                entry = self._attrs.get(aid)
                if entry is None:
                    raise AttributeValidationError(f"未知属性 0x{aid & 0xFF:02X}")
                # validate_attr_value 使用同一把 RLock，可重入
                normalized[aid] = self.validate_attr_value(aid, value)

            old_values: dict[int, Any] = {}
            for aid, value in normalized.items():
                entry = self._attrs[aid]
                old_values[aid] = copy.deepcopy(entry.current_value)
                entry.current_value = value
            return old_values

    def restore_values(self, old_values: dict[int, Any]) -> None:
        """在同一把锁下恢复一组属性值（事务回滚）。

        忽略快照中已不存在的 attrid；不重新校验，避免回滚路径因校验
        规则变化而再次失败。
        """
        if not old_values:
            return
        with self._lock:
            for attrid, value in old_values.items():
                entry = self._attrs.get(int(attrid))
                if entry is not None:
                    entry.current_value = value

    def get_snapshot(self) -> dict[int, Any]:
        """返回全部属性 current_value 的深拷贝快照（事务起点）。"""
        with self._lock:
            return {
                aid: copy.deepcopy(entry.current_value)
                for aid, entry in self._attrs.items()
            }

    def restore_snapshot(self, snapshot: dict[int, Any]) -> None:
        """用完整快照原子恢复属性值（与 get_snapshot 配对）。"""
        self.restore_values(snapshot)

    def set_batch_value(self, attrid: int, value: Any) -> None:
        with self._lock:
            entry = self._attrs.get(int(attrid))
            if entry is not None:
                entry.batch_value = value

    def get_readable_attrs(self) -> list[int]:
        with self._lock:
            return [aid for aid in self._attr_order if self._attrs[aid].access != "只写"]

    def get_writable_attrs(self) -> list[int]:
        with self._lock:
            return [aid for aid in self._attr_order if self._attrs[aid].access in ("读写", "只写")]

    def get_all_attrs(self) -> list[AttrEntry]:
        with self._lock:
            return [
                replace(
                    self._attrs[aid],
                    enum=copy.deepcopy(self._attrs[aid].enum),
                    current_value=copy.deepcopy(self._attrs[aid].current_value),
                    batch_value=copy.deepcopy(self._attrs[aid].batch_value),
                )
                for aid in self._attr_order
            ]

    def reset_heartbeat_counter(self) -> None:
        with self._lock:
            self._heartbeat_count = 0

    def increment_heartbeat(self) -> int:
        """Atomically increment and return the heartbeat sequence number."""
        with self._lock:
            self._heartbeat_count += 1
            return self._heartbeat_count

    @property
    def generation(self) -> int:
        """Monotonic product/protocol generation. Bumped on successful load_product."""
        with self._lock:
            return self._generation

    @property
    def heartbeat_count(self) -> int:
        with self._lock:
            return self._heartbeat_count

    @heartbeat_count.setter
    def heartbeat_count(self, value: int) -> None:
        with self._lock:
            self._heartbeat_count = max(0, int(value))
