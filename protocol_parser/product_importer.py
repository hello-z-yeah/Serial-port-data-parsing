"""产品功能 JSON 导入器。

把 PID、Model、MCU 版本以及功能定义 JSON 转换成当前项目可加载的
产品协议配置。底层帧格式和命令格式继续由内置 ``v3_serial.json``
在 GUI 加载时合并，本模块只负责产品属性和 ``product_info``。
"""
from __future__ import annotations
from .exceptions import ProductConfigError

import json
import re
from pathlib import Path
from typing import Any


FORMAT_TO_TYPEID = {
    "bool": 0,
    "int8": 1,
    "uint8": 2,
    "int16": 3,
    "uint16": 4,
    "int32": 5,
    "uint32": 6,
    "int64": 7,
    "uint64": 8,
    "float": 9,
    "float32": 9,
    "double": 10,
    "float64": 10,
    "string": 11,
    "date": 12,
    "struct": 13,
    "array": 14,
    "float.one_decimal": 15,
    "float.two_decimal": 16,
}

ACCESS_MAP = {
    "read": "只读",
    "write": "只写",
    "readwrite": "读写",
    "read_write": "读写",
    "notify": "只读",
}


# 常见米家/物联网功能名称中文化。JSON 中若本身包含中文则原样保留；
# 英文描述优先按完整短语翻译，无法识别时使用中文兜底名称，避免实时属性表
# 再出现整列英文或 ``prop_1`` 之类的占位符。
_LABEL_PHRASE_MAP = {
    "device information": "设备信息",
    "device manufacturer": "设备制造商",
    "device model": "设备型号",
    "device id": "设备ID",
    "current firmware version": "当前固件版本",
    "firmware version": "固件版本",
    "serial number": "序列号",
    "night light switch": "夜灯开关",
    "switch status": "开关状态",
    "ptc bath heater": "PTC浴霸",
    "target temperature": "目标温度",
    "fan control": "风机控制",
    "fan level": "风速档位",
    "horizontal swing": "左右摆风",
    "vertical swing": "上下摆风",
    "recommended standard": "推荐标准",
    "remote controllable": "远程控制",
    "temperature": "温度",
    "brightness": "亮度",
    "on": "开关",
    "off": "关闭",
    "color temperature": "色温",
    "color": "颜色",
    "speed": "速度",
    "timer": "定时",
    "countdown": "倒计时",
    "child lock": "童锁",
    "fault": "故障",
    "alarm": "告警",
    "air quality": "空气质量",
    "wind speed": "风速",
    "wind direction": "风向",
    "humidity": "湿度",
    "power": "电源",
    "switch": "切换",
    "status": "状态",
    "mode": "模式",
    "light": "照明",
    "custom": "自定义",
    "manufacturer": "制造商",
    "model": "型号",
    "version": "版本",
    "device": "设备",
    "information": "信息",
    "current": "当前",
    "target": "目标",
    "level": "档位",
    "control": "控制",
    "remote": "远程",
    # 属性配置导出文件中常见的 attributeKey / attributeName。
    "volume": "音量",
    "play": "播放",
    "playing": "播放",
    "play control": "播放控制",
    "now playing": "当前播放",
    "play mode": "播放模式",
    "source switching": "音源切换",
    "sound mode": "音效模式",
    "speaker config": "扬声器配置",
    "equalizer config": "均衡器配置",
    "song list one": "歌曲列表一",
    "song list two": "歌曲列表二",
    "song list three": "歌曲列表三",
    "song control": "歌曲控制",
    "new song": "新歌曲",
    "search list one": "搜索列表一",
    "search list two": "搜索列表二",
    "oneclick switch": "一键切换",
    "one click switch": "一键切换",
    "play prompt": "播放提示",
    "reduce volume": "降低音量",
    "play local list": "播放本地列表",
    "play list": "播放列表",
    "voice control": "语音控制",
    "screen control": "屏幕控制",
    "screen lightness": "屏幕亮度",
    "volume reduce increa": "音量增减",
    "scene": "场景",
    # 家电/晾衣机等平台导出中常见名称。
    "memory location": "记忆位置",
    "memory location one": "记忆位置一",
    "memory location two": "记忆位置二",
    "conceal": "隐藏",
    "motor control": "电机控制",
    "device fault": "设备故障",
    "fault status": "故障状态",
    "drying": "烘干",
    "sterilization": "消毒",
    "air drying": "风干",
    "clothes rail": "晾衣杆",
    "lift control": "升降控制",
    "up": "上升",
    "down": "下降",
    "stop": "停止",
}


def _normalized_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _first_text(meta: dict, *keys: str) -> str:
    """按原键与大小写/分隔符无关的别名读取首个非空文本。"""
    if not isinstance(meta, dict):
        return ""
    for key in keys:
        value = meta.get(key)
        if value not in (None, ""):
            return str(value).strip()
    normalized = {_normalized_key(key): value for key, value in meta.items()}
    for key in keys:
        value = normalized.get(_normalized_key(key))
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _attribute_source_text(meta: dict, fallback: str = "") -> str:
    """从常见平台字段中保留真实属性名称，避免退化为属性0xXX。"""
    return _first_text(
        meta,
        "source_attribute_name", "sourceAttributeName",
        "attributeName", "attribute_name",
        "displayName", "display_name",
        "propertyName", "property_name",
        "functionName", "function_name",
        "cn_name", "cnName", "label", "title",
        "attributeDesc", "attribute_desc", "description", "desc",
        "source_attribute_key", "sourceAttributeKey",
        "attributeKey", "attribute_key",
        "propertyKey", "property_key", "name", "key", "identifier",
    ) or str(fallback or "")


def _contains_cjk(text: str) -> bool:
    return any("\u3400" <= ch <= "\u9fff" for ch in str(text or ""))


def localized_attribute_name(value: Any, *, fallback: str = "属性") -> str:
    """把属性/服务英文描述转换成适合界面显示的中文名称。

    该函数只负责显示名称，不改变协议中的 attrID/typeid。对无法可靠翻译的
    英文内容使用调用方给出的中文兜底，保证“名称”列不会退回英文占位符。
    """
    text = str(value or "").strip()
    if not text:
        return str(fallback or "属性")
    if _contains_cjk(text):
        return text

    # 将驼峰、下划线和常见分隔符统一成便于匹配的形式。
    normalized = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    normalized = normalized.replace("_", " ").replace("/", "-")

    # 先按完整 attributeKey / attributeName 匹配。很多属性使用连字符，
    # 若先拆分会把 ``play-control`` 变成两个残缺词组，最终退回“属性0xXX”。
    exact_key = re.sub(r"[\s\-]+", " ", normalized.strip().lower()).strip()
    exact_value = _LABEL_PHRASE_MAP.get(exact_key)
    if exact_value:
        return exact_value

    segments = [seg.strip() for seg in re.split(r"\s*-\s*", normalized) if seg.strip()]
    translated_segments: list[str] = []
    for segment in segments or [normalized]:
        lowered = re.sub(r"\s+", " ", segment.strip().lower())
        translated = _LABEL_PHRASE_MAP.get(lowered)
        if translated is None:
            # 先按长短语替换，再检查是否仍残留无法识别的英文单词。
            translated = lowered
            for phrase in sorted(_LABEL_PHRASE_MAP, key=len, reverse=True):
                translated = re.sub(
                    rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])",
                    _LABEL_PHRASE_MAP[phrase],
                    translated,
                    flags=re.IGNORECASE,
                )
            translated = re.sub(r"\s+", "", translated)
            # 无法可靠翻译时保留原始名称，而不是退化为“属性0xXX”。
            # 原文至少能让用户识别功能，也便于后续补充词典。
            residue = re.sub(r"(?:PTC|ID|MCU|PID|[0-9]+)", "", translated, flags=re.I)
            if re.search(r"[A-Za-z]", residue):
                translated = segment.strip() or text
        translated_segments.append(translated or segment.strip() or text)

    result = "-".join(translated_segments)
    result = re.sub(r"-{2,}", "-", result).strip("- ")
    return result or text or str(fallback or "属性")


def localize_attributes(attributes: dict | None) -> dict:
    """就地补齐属性中文名称，并返回同一个字典。"""
    attrs = attributes if isinstance(attributes, dict) else {}
    for raw_id, meta in attrs.items():
        if str(raw_id).startswith("__") or not isinstance(meta, dict):
            continue
        fallback = f"属性{raw_id}"
        source = _attribute_source_text(meta, fallback=fallback)
        meta["cn_name"] = localized_attribute_name(source, fallback=fallback)
    return attrs


def _load_json_value(raw_json: str | dict | list) -> Any:
    if not isinstance(raw_json, str):
        return raw_json
    text = raw_json.lstrip("\ufeff").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return json.loads(text)




def _decode_json_layers(value: Any, *, max_depth: int = 4) -> Any:
    """Decode JSON strings repeatedly while preserving ordinary text.

    Some product-export tools wrap the actual function JSON in a string field,
    and older saved products can therefore contain one or two JSON-encoded
    layers. Device-information metadata must still be recoverable from those
    files instead of silently regenerating a different 0x21 mapping.
    """
    current = value
    for _ in range(max(1, int(max_depth))):
        if not isinstance(current, str):
            break
        text = current.lstrip("\ufeff").strip()
        if not text or text[:1] not in ("{", "[", '"'):
            break
        try:
            decoded = json.loads(text)
        except (TypeError, ValueError, json.JSONDecodeError):
            break
        if decoded == current:
            break
        current = decoded
    return current


def _casefold_dict_get(mapping: dict, *keys: str) -> Any:
    wanted = {_normalized_key(key) for key in keys}
    for key, value in mapping.items():
        if _normalized_key(key) in wanted:
            return value
    return None


def _find_device_info_base(value: Any, *, max_depth: int = 6) -> dict | None:
    """Find the export ``Base`` object in common wrapped JSON shapes.

    The lookup is case/separator insensitive and also handles ``data``/``result``
    wrappers and JSON-encoded string fields.  A dictionary is accepted as the
    Base object only when it contains ``expandRules`` or ``version`` so an
    unrelated nested object named ``base`` cannot be selected accidentally.
    """
    root = _decode_json_layers(value)
    queue: list[tuple[Any, int]] = [(root, 0)]
    seen: set[int] = set()
    while queue:
        current, depth = queue.pop(0)
        current = _decode_json_layers(current)
        if isinstance(current, (dict, list)):
            marker = id(current)
            if marker in seen:
                continue
            seen.add(marker)
        if isinstance(current, dict):
            direct = _casefold_dict_get(current, "Base")
            direct = _decode_json_layers(direct)
            if isinstance(direct, dict) and (
                _casefold_dict_get(direct, "expandRules", "expand_rules") not in (None, "")
                or _casefold_dict_get(direct, "version", "mcuVersion", "mcu_version") not in (None, "")
            ):
                return direct
            if (
                _casefold_dict_get(current, "expandRules", "expand_rules") not in (None, "")
                or _casefold_dict_get(current, "version") not in (None, "")
            ) and depth > 0:
                return current
            if depth < max_depth:
                preferred = ("data", "result", "payload", "content", "config", "functionJson", "function_json")
                preferred_values = []
                other_values = []
                preferred_keys = {_normalized_key(key) for key in preferred}
                for key, nested in current.items():
                    target = preferred_values if _normalized_key(key) in preferred_keys else other_values
                    if isinstance(nested, (dict, list, str)):
                        target.append((nested, depth + 1))
                queue.extend(preferred_values + other_values)
        elif isinstance(current, list) and depth < max_depth:
            queue.extend((item, depth + 1) for item in current if isinstance(item, (dict, list, str)))
    return None


def normalize_expand_rules(raw: Any) -> str:
    """Return canonical uppercase HEX for ``Base.expandRules``.

    The function deliberately rejects malformed or truncated values. Silently
    falling back to a regenerated mapping changes serial order/SIID/PIID and can
    make the module reject the device-information reply.
    """
    if isinstance(raw, (bytes, bytearray)):
        data = bytes(raw)
    elif isinstance(raw, (list, tuple)):
        try:
            data = bytes(int(value) & 0xFF for value in raw)
        except (TypeError, ValueError) as exc:
            raise ProductConfigError("Base.expandRules 字节数组包含非法值") from exc
    else:
        text = str(raw or "").strip()
        if not text:
            return ""
        clean = re.sub(r"(?i)0x", "", text)
        clean = re.sub(r"[\s,;:_-]+", "", clean)
        if not clean or len(clean) % 2:
            raise ProductConfigError("Base.expandRules 必须是偶数位十六进制字符串")
        if re.search(r"[^0-9a-fA-F]", clean):
            raise ProductConfigError("Base.expandRules 包含非十六进制字符")
        try:
            data = bytes.fromhex(clean)
        except ValueError as exc:
            raise ProductConfigError("Base.expandRules 不是有效十六进制数据") from exc
    if len(data) < 10:
        raise ProductConfigError("Base.expandRules 数据过短")
    # Exact device information exports must contain PID(F7), Model(F5) and
    # attribute-map(F3) TLVs.  Keep the original byte sequence unchanged.
    for marker, label in ((b"\x06\xF7", "PID(F7)"), (b"\x0B\xF5", "Model(F5)"), (b"\x0E\xF3", "属性映射(F3)")):
        if marker not in data:
            raise ProductConfigError(f"Base.expandRules 缺少 {label} 字段")
    return data.hex(" ").upper()


def _normalize_device_version(raw: Any) -> list[int]:
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("["):
            try:
                raw = json.loads(text)
            except Exception:
                raw = text
        if isinstance(raw, str):
            parts = re.split(r"[.\s,_-]+", raw)
        else:
            parts = list(raw) if isinstance(raw, (list, tuple)) else [raw]
    elif isinstance(raw, (list, tuple)):
        parts = list(raw)
    elif raw in (None, ""):
        return []
    else:
        parts = [raw]
    result: list[int] = []
    for part in parts[:3]:
        try:
            value = int(str(part), 0)
        except (TypeError, ValueError):
            return []
        if not 0 <= value <= 99:
            raise ProductConfigError(f"Base.version 超出 0–99 范围：{value}")
        result.append(value)
    while result and len(result) < 3:
        result.append(0)
    return result


def extract_device_info_metadata(raw_json: Any) -> dict[str, Any]:
    """Extract the exact 0x21 source metadata from imported function JSON.

    Returned keys are ``expand_rules`` (canonical HEX), ``version`` (3 bytes),
    ``pid`` and ``model``.  Missing metadata is represented by empty values.
    """
    base = _find_device_info_base(raw_json)
    result: dict[str, Any] = {"expand_rules": "", "version": [], "pid": "", "model": ""}
    if not isinstance(base, dict):
        return result
    expand_raw = _casefold_dict_get(base, "expandRules", "expand_rules")
    if expand_raw not in (None, ""):
        result["expand_rules"] = normalize_expand_rules(expand_raw)
        parsed = parse_expand_rules(result["expand_rules"])
        result["pid"] = parsed.get("pid", "")
        result["model"] = parsed.get("model", "")
    version_raw = _casefold_dict_get(base, "version", "mcuVersion", "mcu_version")
    result["version"] = _normalize_device_version(version_raw)
    return result


def _normalize_access(value: Any) -> str:
    if isinstance(value, (list, tuple, set)):
        words = {str(v).strip().lower() for v in value}
        readable = bool(words & {"read", "notify"})
        writable = "write" in words
        if readable and writable:
            return "读写"
        if writable:
            return "只写"
        return "只读"
    text = str(value or "read").strip()
    if text in ("只读", "只写", "读写"):
        return text
    return ACCESS_MAP.get(text.lower(), "只读")


def _normalize_attr_key(raw: Any) -> str:
    if isinstance(raw, str):
        text = raw.strip()
        value = int(text, 16) if text.lower().startswith("0x") else int(text, 0)
    else:
        value = int(raw)
    if not 0 <= value <= 0xFF:
        raise ProductConfigError(f"attrid 超出 1 字节范围：{raw!r}")
    return f"0x{value:02X}"



_ENUM_KEYS = (
    "enum", "value_list", "value-list", "valueList", "valuelist",
    "values", "options", "mapping", "enum_values", "enumValues",
)
_RANGE_KEYS = (
    "range", "value_range", "value-range", "valueRange", "valuerange",
)
_META_CONTAINER_KEYS = (
    "value", "value_schema", "valueSchema", "schema", "constraints",
    "constraint", "spec", "property", "data_type", "dataType",
)


def _metadata_containers(meta: dict) -> list[dict]:
    """Return the property metadata plus common nested constraint objects."""
    if not isinstance(meta, dict):
        return []
    result = [meta]
    seen = {id(meta)}
    queue = [meta]
    while queue:
        current = queue.pop(0)
        for key in _META_CONTAINER_KEYS:
            nested = current.get(key)
            if isinstance(nested, dict) and id(nested) not in seen:
                seen.add(id(nested))
                result.append(nested)
                queue.append(nested)
    return result


def _enum_item_text(item: dict, fallback: Any = "") -> str:
    for key in (
        "description", "cn_name", "display_name", "displayName", "label",
        "text", "title", "name", "desc",
    ):
        value = item.get(key)
        if value not in (None, ""):
            return str(value)
    return str(fallback)


def extract_enum_map(meta: dict) -> dict[str, str]:
    """Read enum/value-list definitions from common IoT JSON dialects.

    In particular, Xiaomi-style function JSON commonly uses ``value-list``
    (with a hyphen), while older project code only recognized ``value_list``.
    """
    enum_value: Any = None
    description_values: Any = None
    for container in _metadata_containers(meta):
        for key in _ENUM_KEYS:
            if key in container and container.get(key) not in (None, ""):
                enum_value = container.get(key)
                break
        if enum_value is not None:
            for key in (
                "value_descriptions", "valueDescriptions", "descriptions",
                "enum_descriptions", "enumDescriptions", "labels",
            ):
                if container.get(key) not in (None, ""):
                    description_values = container.get(key)
                    break
            break

    if isinstance(enum_value, str):
        text = enum_value.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                enum_value = json.loads(text)
            except Exception:
                pass
        if isinstance(enum_value, str):
            result: dict[str, str] = {}
            for part in re.split(r"[,，;；]", text):
                part = part.strip()
                if not part:
                    continue
                if ":" in part:
                    key, value = part.split(":", 1)
                elif "=" in part:
                    key, value = part.split("=", 1)
                else:
                    continue
                result[key.strip()] = value.strip()
            return result

    if isinstance(enum_value, dict):
        result: dict[str, str] = {}
        for raw_key, raw_value in enum_value.items():
            if isinstance(raw_value, dict):
                result[str(raw_key)] = _enum_item_text(raw_value, raw_key)
            else:
                result[str(raw_key)] = str(raw_value)
        return result

    if isinstance(enum_value, (list, tuple)):
        result: dict[str, str] = {}
        descriptions = list(description_values) if isinstance(description_values, (list, tuple)) else []
        for index, item in enumerate(enum_value):
            if isinstance(item, dict):
                raw_value = None
                for key in ("value", "id", "key", "code", "index"):
                    if item.get(key) not in (None, ""):
                        raw_value = item.get(key)
                        break
                if raw_value is None:
                    continue
                result[str(raw_value)] = _enum_item_text(item, raw_value)
            else:
                label = descriptions[index] if index < len(descriptions) else item
                result[str(item)] = str(label)
        return result

    return {}


def extract_range_value(meta: dict) -> Any:
    """Read numeric range definitions, including ``value-range`` aliases."""
    for container in _metadata_containers(meta):
        for key in _RANGE_KEYS:
            value = container.get(key)
            if value not in (None, ""):
                return value
        minimum = container.get("min", container.get("minimum"))
        maximum = container.get("max", container.get("maximum"))
        if minimum not in (None, "") and maximum not in (None, ""):
            step = container.get("step")
            return [minimum, maximum, step] if step not in (None, "") else [minimum, maximum]
    return ""


def _normalize_attr_entry(meta: dict, *, fallback_name: str = "") -> dict:
    fmt = str(meta.get("format") or meta.get("type") or "").strip().lower()
    typeid_raw = meta.get("typeid", FORMAT_TO_TYPEID.get(fmt, 2))
    try:
        typeid = int(typeid_raw)
    except (TypeError, ValueError):
        typeid = FORMAT_TO_TYPEID.get(str(typeid_raw).lower(), 2)

    source_text = _attribute_source_text(meta, fallback=fallback_name)
    raw_name = str(_first_text(meta, "name", "attributeName", "attributeKey") or source_text or fallback_name or "")
    cn_candidate = str(meta.get("cn_name") or "").strip()
    raw_original_name = str(
        meta.get("original_name")
        or source_text
        or (cn_candidate if cn_candidate and not _contains_cjk(cn_candidate) else "")
        or raw_name
        or fallback_name
        or ""
    )
    raw_display_name = source_text or cn_candidate or raw_name
    fallback_display = f"属性{fallback_name}" if fallback_name else "属性"
    entry = {
        "name": raw_name,
        "original_name": raw_original_name,
        "cn_name": localized_attribute_name(raw_display_name, fallback=fallback_display),
        "typeid": typeid,
        "access": _normalize_access(meta.get("access", "read")),
    }
    for source_key in ("source_attribute_key", "source_attribute_name"):
        source_value = str(meta.get(source_key) or "").strip()
        if source_value:
            entry[source_key] = source_value

    range_value = extract_range_value(meta)
    if isinstance(range_value, (list, tuple)) and len(range_value) >= 2:
        entry["range"] = f"[{range_value[0]},{range_value[1]}]"
        step_value = range_value[2] if len(range_value) >= 3 else meta.get("step")
        if step_value not in (None, ""):
            entry["step"] = step_value
    elif isinstance(range_value, dict):
        minimum = range_value.get("min", range_value.get("minimum"))
        maximum = range_value.get("max", range_value.get("maximum"))
        if minimum not in (None, "") and maximum not in (None, ""):
            entry["range"] = f"[{minimum},{maximum}]"
            step_value = range_value.get("step", meta.get("step"))
            if step_value not in (None, ""):
                entry["step"] = step_value
        else:
            entry["range"] = json.dumps(range_value, ensure_ascii=False)
    elif range_value not in (None, ""):
        entry["range"] = str(range_value)

    enum_map = extract_enum_map(meta)
    if enum_map:
        entry["enum"] = enum_map

    unit = meta.get("unit", "")
    if unit not in (None, ""):
        entry["unit"] = str(unit)

    for key in ("length_width", "scale"):
        if key in meta:
            entry[key] = meta[key]
    return entry


def parse_function_json(raw_json: str | dict | list, platform: str = "xiaomi") -> dict:
    """解析功能定义 JSON，返回标准 ``attributes`` 字典。

    支持：
    1. 米家 ``services -> properties`` 格式；
    2. ``{"0x00": {...}}`` 属性字典；
    3. ``[{"attrid": "0x00", ...}]`` 属性数组。
    """
    del platform  # 预留将来平台映射扩展
    data = _load_json_value(raw_json)
    attributes: dict[str, dict] = {}

    if isinstance(data, dict) and isinstance(data.get("services"), list):
        used_ids: set[int] = set()
        for service in data["services"]:
            if not isinstance(service, dict):
                continue
            siid = int(service.get("iid", service.get("siid", 0)) or 0)
            service_name = str(service.get("description") or service.get("name") or "")
            service_cn_name = localized_attribute_name(
                service_name,
                fallback=f"服务{siid}",
            )
            for prop in service.get("properties", []) or []:
                if not isinstance(prop, dict):
                    continue
                piid = int(prop.get("iid", prop.get("piid", 0)) or 0)
                attrid = ((siid * 16 + piid) + 0x20) & 0xFF
                while attrid in used_ids and attrid < 0xFF:
                    attrid += 1
                used_ids.add(attrid)
                meta = dict(prop)
                prop_name = str(meta.get("description") or meta.get("name") or piid)
                meta.setdefault(
                    "original_name",
                    f"{service_name}-{prop_name}" if service_name else prop_name,
                )
                if not meta.get("cn_name"):
                    prop_cn_name = localized_attribute_name(
                        prop_name,
                        fallback=f"功能{piid}",
                    )
                    meta["cn_name"] = (
                        f"{service_cn_name}-{prop_cn_name}"
                        if service_name else prop_cn_name
                    )
                attributes[f"0x{attrid:02X}"] = _normalize_attr_entry(
                    meta, fallback_name=f"prop_{piid}"
                )

    elif isinstance(data, dict) and data and all(
        isinstance(k, str) and (k.lower().startswith("0x") or k.strip().isdigit())
        for k in data.keys()
    ):
        for raw_key, raw_meta in data.items():
            if raw_key == "__length_width__":
                attributes[raw_key] = raw_meta
                continue
            if not isinstance(raw_meta, dict):
                raise ProductConfigError(f"属性 {raw_key} 的定义必须是对象")
            key = _normalize_attr_key(raw_key)
            attributes[key] = _normalize_attr_entry(raw_meta, fallback_name=key)

    elif isinstance(data, dict) and isinstance(data.get("Attrs"), list):
        # 处理 {"Base": ..., "Attrs": [...], "ActionEvent": [...]} 格式
        # （如 wise/滑客等平台导出的功能定义 JSON）
        for index, item in enumerate(data["Attrs"]):
            if not isinstance(item, dict):
                raise ProductConfigError(f"Attrs 数组第 {index + 1} 项不是对象")
            raw_id = item.get("serialId", item.get("attrid", item.get("id")))
            if raw_id is None:
                raise ProductConfigError(f"Attrs 数组第 {index + 1} 项缺少 serialId/attrid")
            key = _normalize_attr_key(raw_id)

            meta: dict[str, Any] = {}
            attribute_key = _first_text(
                item, "attributeKey", "attribute_key", "propertyKey",
                "property_key", "key", "identifier",
            )
            attribute_name = _first_text(
                item, "attributeName", "attribute_name", "displayName",
                "display_name", "propertyName", "property_name",
                "functionName", "function_name", "name", "title", "label",
            )
            attribute_desc = _first_text(
                item, "attributeDesc", "attribute_desc", "description", "desc"
            )
            source_text = attribute_name or attribute_desc or attribute_key
            if source_text:
                meta["name"] = source_text
                meta["original_name"] = source_text
                meta["cn_name"] = localized_attribute_name(
                    source_text, fallback=f"属性{key}"
                )
            if attribute_key:
                meta["source_attribute_key"] = attribute_key
            if attribute_name:
                meta["source_attribute_name"] = attribute_name
            if attribute_desc:
                meta["description"] = attribute_desc

            # dataRwx: r/w/rw → read/write/readwrite
            rwx = str(item.get("dataRwx", "r")).strip().lower()
            has_r = "r" in rwx
            has_w = "w" in rwx
            meta["access"] = (
                "readwrite" if has_r and has_w
                else "write" if has_w
                else "read"
            )

            # type 字段直接是 typeid（2=uint8, 11=string …）
            try:
                meta["typeid"] = int(item.get("type", 2))
            except (TypeError, ValueError):
                meta["typeid"] = 2
            # dataType 作为 format 辅助信息
            dtype = str(item.get("dataType", "") or "").strip().lower()
            if dtype:
                meta["format"] = dtype

            # dataValue 是 JSON 字符串，可能是 range/enum/length
            dv = item.get("dataValue", "")
            if isinstance(dv, str) and dv.strip():
                try:
                    parsed_dv = json.loads(dv)
                except Exception:
                    parsed_dv = None
                if isinstance(parsed_dv, dict):
                    if "min" in parsed_dv or "max" in parsed_dv:
                        meta["value_range"] = parsed_dv
                    elif "length" in parsed_dv:
                        meta["value_range"] = parsed_dv
                    else:
                        # 纯 key-value 映射 → enum
                        meta["enum"] = parsed_dv

            normalized = _normalize_attr_entry(meta, fallback_name=key)
            # 保留导出工具中的线协议属性号和当前值。0x24 快照必须使用
            # serialId，而不是由其他平台规则换算出的内部属性号。
            try:
                normalized["snapshot_wire_id"] = int(raw_id) & 0xFF
            except (TypeError, ValueError):
                pass
            if "nowValue" in item:
                normalized["initial_value"] = item.get("nowValue")
            normalized["snapshot_include"] = bool(has_r)
            normalized["source_data_rwx"] = rwx
            if dtype:
                normalized["source_data_type"] = dtype
            attributes[key] = normalized

    elif isinstance(data, list):
        for index, item in enumerate(data):
            if not isinstance(item, dict):
                raise ProductConfigError(f"属性数组第 {index + 1} 项不是对象")
            raw_id = item.get("attrid", item.get("id", item.get("attr_id")))
            if raw_id is None:
                raise ProductConfigError(f"属性数组第 {index + 1} 项缺少 attrid/id")
            key = _normalize_attr_key(raw_id)
            attributes[key] = _normalize_attr_entry(item, fallback_name=key)
    else:
        raise ProductConfigError("无法识别的 JSON 格式：需要 services、属性字典或属性数组")

    real_attrs = [k for k in attributes if k != "__length_width__"]
    if not real_attrs:
        raise ProductConfigError("功能定义中没有可导入的属性")
    return localize_attributes(attributes)


def parse_expand_rules(hex_str: str) -> dict:
    """解析 expandRules 十六进制字符串，提取设备信息。

    格式为 TLV 连续排列::

        06 F7 00 00 <pid 2 bytes>          # PID 字段
        0B F5 00 <len> <model ascii>        # Model 字段
        0E F3 00 <table_len> <header 2B>   # 属性映射表

    返回 ``{"pid": str, "model": str}``，解析失败的字段为空字符串。
    """
    result: dict[str, str] = {"pid": "", "model": ""}
    try:
        clean = hex_str.strip()
        data = bytes.fromhex(clean.replace(" ", ""))
    except (ValueError, AttributeError):
        return result

    pos = 0
    while pos + 3 < len(data):
        tag = data[pos]
        if tag == 0x06 and pos + 5 < len(data):
            # F7 <pad 00> <00> <pid_hi> <pid_lo>
            pid_val = (data[pos + 4] << 8) | data[pos + 5]
            result["pid"] = str(pid_val)
            pos += 6
        elif tag == 0x0B and pos + 3 < len(data):
            # F5 00 <len> <ascii...>
            slen = data[pos + 3]
            s_start = pos + 4
            s_end = s_start + slen
            if s_end <= len(data):
                result["model"] = data[s_start:s_end].decode("ascii", errors="replace")
            pos = s_end
        elif tag == 0x0E:
            # 属性映射表，后面是变长数据，直接跳出
            break
        else:
            pos += 1
    return result


def _parse_version(version: str | list | tuple) -> list[int]:
    if isinstance(version, (list, tuple)):
        parts = list(version)
    else:
        parts = str(version or "1.0.0").strip().split(".")
    resolved: list[int] = []
    for part in parts[:3]:
        try:
            value = int(part)
        except (TypeError, ValueError):
            value = 0
        resolved.append(max(0, min(255, value)))
    while len(resolved) < 3:
        resolved.append(0)
    return resolved


def build_product_cfg(
    *,
    product_name: str,
    pid: str,
    model: str,
    attributes: dict,
    mcu_version: str = "1.0.0",
    description: str = "",
) -> dict:
    """组装可与内置 V3 协议合并的产品配置。"""
    product_name = str(product_name or model or pid or "未命名产品").strip()
    return {
        "product": product_name,
        "import_source": "json",
        "description": description or f"PID={pid}, Model={model}",
        "version": "1.0",
        "frame": {
            "header": "0xA5A5",
            "header_size": 2,
            "ver": "0x03",
            "ver_offset": 2,
            "ver_size": 1,
            "cmd_offset": 3,
            "length_offset": 4,
            "length_size": 2,
            "length_byte_order": "big",
            "checksum": {
                "algorithm": "sum",
                "length": 1,
                "covers": "from_start_to_checksum_exclusive",
            },
        },
        "enums": {},
        "commands": [],
        "attributes": attributes,
        "product_info": {
            "pid": str(pid or ""),
            "model": str(model or ""),
            "mcu_version": _parse_version(mcu_version),
            "json_version": 1,
            # V3.0 设备信息回复包含 3 字节设备版本前缀。
            # 特殊旧设备如明确不需要，可在产品文件中手动设为 false。
            "include_version_prefix": True,
        },
    }


def safe_protocol_filename(product_name: str, *, model: str = "", pid: str = "") -> str:
    """生成 Windows/ZIP 安全的产品 JSON 文件名。"""
    preferred = str(model or pid or product_name or "product").strip()
    preferred = re.sub(r'[<>:"/\\|?*\x00-\x1F]+', "_", preferred)
    preferred = re.sub(r"\s+", "_", preferred).strip(" ._")
    if not preferred:
        preferred = "product"
    return preferred[:96] + ".json"


def save_product_cfg(path: str | Path, cfg: dict) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return target
