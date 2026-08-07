"""0x21 设备信息回复帧编码器。

设备信息 data 段由以下部分组成：
- 设备版本 3B
- PID 扩展属性（0xF7）
- Model 扩展属性（0xF5）
- 属性映射表（0xF3）

米家 ``services -> properties`` 产品的映射项必须使用 6 字节格式：
``typeid + serial_index + siid(低字节在前) + piid(高字节在前)``。
旧的 3 字节简化格式仅保留给无法取得 SIID/PIID 的非米家产品。
"""
from __future__ import annotations

import json
import struct
import warnings

from .parser import ProtocolConfigError, encode_frame
from .product_importer import extract_device_info_metadata, normalize_expand_rules


def _pid_to_uint32(pid: object) -> int:
    text = str(pid or "").strip()
    if not text:
        return 0
    try:
        if text.lower().startswith("0x"):
            value = int(text, 16)
        else:
            value = int(text, 10)  # 强制十进制，避免 "00123" 被 base=0 当成八进制失败
    except ValueError as exc:
        raise ProtocolConfigError(
            f"产品 PID 必须是十进制或 0x 前缀整数，实际为 {text!r}"
        ) from exc
    if not 0 <= value <= 0xFFFFFFFF:
        raise ProtocolConfigError(f"产品 PID 超出 UINT32 范围：{value}")
    return value


def _version_bytes(cfg: dict) -> bytes:
    info = cfg.get("product_info") or {}
    raw = info.get("device_info_version")
    if raw in (None, "", []):
        # Existing saved products may predate the dedicated metadata fields but
        # still keep the original function JSON. Recover Base.version at encode
        # time so they are fixed without forcing the user to re-import.
        try:
            source_metadata = extract_device_info_metadata(
                cfg.get("source_function_json") or cfg
            )
        except Exception as exc:
            raise ProtocolConfigError(f"设备信息 Base.version 解析失败：{exc}") from exc
        raw = source_metadata.get("version") or info.get("mcu_version", [1, 0, 0])
    if not isinstance(raw, (list, tuple)):
        raw = str(raw).split(".")
    values: list[int] = []
    for part in list(raw)[:3]:
        try:
            value = int(part)
        except (TypeError, ValueError) as exc:
            raise ProtocolConfigError(f"设备信息版本字节无效：{part!r}") from exc
        if not 0 <= value <= 99:
            raise ProtocolConfigError(f"设备信息版本字节超出 0–99：{value}")
        values.append(value)
    while len(values) < 3:
        values.append(0)
    return bytes(values)


def _normalize_attr_key(raw: object) -> str:
    text = str(raw or "").strip()
    try:
        value = int(text, 16) if text.lower().startswith("0x") else int(text)
    except (TypeError, ValueError):
        return text.upper()
    return f"0x{value & 0xFF:02X}"


def _load_source_json(cfg: dict) -> dict | None:
    raw = cfg.get("source_function_json")
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _build_miot_source_map(cfg: dict) -> dict[str, tuple[int, int]]:
    """按 product_importer 的分配规则恢复 attrid -> (siid, piid)。

    导入器会扫描完整 services/properties 后生成内部 attrid；属性编辑器只
    保留用户勾选的属性。因此这里也必须扫描完整原始 JSON，才能正确处理
    0x61/0x64/0x6B 一类碰撞后顺延的 attrid。
    """
    source = _load_source_json(cfg)
    services = source.get("services") if isinstance(source, dict) else None
    if not isinstance(services, list):
        return {}

    used_ids: set[int] = set()
    result: dict[str, tuple[int, int]] = {}
    for service in services:
        if not isinstance(service, dict):
            continue
        try:
            siid = int(service.get("iid", service.get("siid", 0)) or 0)
        except (TypeError, ValueError):
            continue
        properties = service.get("properties") or []
        if not isinstance(properties, list):
            continue
        for prop in properties:
            if not isinstance(prop, dict):
                continue
            try:
                piid = int(prop.get("iid", prop.get("piid", 0)) or 0)
            except (TypeError, ValueError):
                continue

            attrid = ((siid * 16 + piid) + 0x20) & 0xFF
            while attrid in used_ids and attrid < 0xFF:
                attrid += 1
            if attrid in used_ids:
                raise ProtocolConfigError(
                    f"MIOT 属性 ID 分配耗尽：siid={siid}, piid={piid}"
                )
            if attrid >= 0xC8:
                warnings.warn(
                    f"MIOT 属性映射 0x{attrid:02X} 进入协议系统保留区，"
                    "请在产品 JSON 中显式配置 serialId/snapshot_wire_id",
                    RuntimeWarning,
                    stacklevel=2,
                )
            used_ids.add(attrid)
            result[f"0x{attrid:02X}"] = (siid, piid)
    return result


def _explicit_source_ids(meta: dict) -> tuple[int, int] | None:
    siid_raw = meta.get("source_siid", meta.get("siid"))
    piid_raw = meta.get("source_piid", meta.get("piid"))
    if siid_raw in (None, "") or piid_raw in (None, ""):
        return None
    try:
        return int(siid_raw), int(piid_raw)
    except (TypeError, ValueError):
        return None



def _explicit_snapshot_wire_id(meta: dict) -> int | None:
    for key in ("snapshot_wire_id", "wire_attrid", "serial_id", "serialId"):
        raw = meta.get(key)
        if raw in (None, ""):
            continue
        try:
            value = int(str(raw), 16) if str(raw).lower().startswith("0x") else int(raw)
        except (TypeError, ValueError):
            continue
        if 0 <= value <= 0xFF:
            return value
    return None


def _source_expand_rules(cfg: dict) -> bytes | None:
    raw = cfg.get("device_info_expand_rules")
    if raw not in (None, ""):
        try:
            return bytes.fromhex(normalize_expand_rules(raw))
        except Exception as exc:
            raise ProtocolConfigError(f"设备信息 expandRules 配置无效：{exc}") from exc

    # Backward-compatible recovery for products saved before the canonical
    # metadata field was added.  The source lookup is case-insensitive, supports
    # wrapper objects and double-encoded JSON, and keeps the exported bytes
    # exactly as-is.  Never silently regenerate a different serial/SIID/PIID
    # order when Base.expandRules is present.
    source = cfg.get("source_function_json")
    if source in (None, ""):
        source = cfg
    try:
        metadata = extract_device_info_metadata(source)
    except Exception as exc:
        raise ProtocolConfigError(f"设备信息 Base.expandRules 解析失败：{exc}") from exc
    expand_rules = metadata.get("expand_rules") or ""
    return bytes.fromhex(expand_rules) if expand_rules else None


def build_snapshot_attrid_map(cfg: dict) -> tuple[dict[int, int], bool]:
    """Return ``internal_attrid -> wire_attrid`` for 0x24 snapshots.

    MIOT ``services/properties`` products advertise a sequential attribute number
    in every 6-byte 0x21 mapping entry. The same sequential number, rather than
    the GUI's internal attrid (for example 0x41/0x51), must be used in the 0x24
    snapshot payload. Legacy/non-MIOT products keep their original attrids.

    The boolean result indicates whether the sequential MIOT mapping is active.
    """
    attributes = [
        (raw_key, meta)
        for raw_key, meta in (cfg.get("attributes") or {}).items()
        if not str(raw_key).startswith("__") and isinstance(meta, dict)
    ]
    if not attributes:
        return {}, False

    # 属性配置导出 JSON 会直接给出 serialId。只要所有保留属性都带
    # snapshot_wire_id，就优先使用该显式映射，不再依赖平台内部 attrid。
    explicit_map: dict[int, int] = {}
    explicit_complete = True
    for raw_key, meta in attributes:
        try:
            internal_attrid = (
                int(str(raw_key), 16)
                if str(raw_key).lower().startswith("0x")
                else int(raw_key)
            ) & 0xFF
        except (TypeError, ValueError):
            explicit_complete = False
            break
        wire_attrid = _explicit_snapshot_wire_id(meta)
        if wire_attrid is None:
            explicit_complete = False
            break
        explicit_map[internal_attrid] = wire_attrid
    if explicit_complete and len(explicit_map) == len(attributes):
        return explicit_map, True

    internal_ids: list[int] = []
    for raw_key, _meta in attributes:
        try:
            internal_ids.append(
                (
                    int(str(raw_key), 16)
                    if str(raw_key).lower().startswith("0x")
                    else int(raw_key)
                ) & 0xFF
            )
        except (TypeError, ValueError):
            return {}, False

    source_map = _build_miot_source_map(cfg)
    all_have_source_ids = True
    for raw_key, meta in attributes:
        source_ids = _explicit_source_ids(meta)
        if source_ids is None:
            source_ids = source_map.get(_normalize_attr_key(raw_key))
        if source_ids is None:
            all_have_source_ids = False
            break

    if not all_have_source_ids:
        return {attrid: attrid for attrid in internal_ids}, False

    return {attrid: index for index, attrid in enumerate(internal_ids)}, True

def _build_attr_mapping(cfg: dict) -> bytes:
    """构建设备信息中的 0xF3 属性映射表。

    米家产品每项 6 字节：
      typeid(1B) + serial_index(1B) + siid(2B, 低字节在前)
      + piid(2B, 高字节在前)

    例如服务 2 / 属性 1 的第 0 个 UINT8 属性：
      ``02 00 02 00 00 01``

    当配置没有原始 services/properties 且属性中也没有 SIID/PIID 时，
    保留旧的 3 字节格式，避免影响其他非米家产品。
    """
    attributes = [
        (raw_key, meta)
        for raw_key, meta in (cfg.get("attributes") or {}).items()
        if not str(raw_key).startswith("__") and isinstance(meta, dict)
    ]
    source_map = _build_miot_source_map(cfg)

    resolved: list[tuple[int, int, int]] = []
    all_have_source_ids = bool(attributes)
    for raw_key, meta in attributes:
        try:
            typeid = int(meta.get("typeid", 2)) & 0xFF
        except (TypeError, ValueError):
            typeid = 2

        source_ids = _explicit_source_ids(meta)
        if source_ids is None:
            source_ids = source_map.get(_normalize_attr_key(raw_key))
        if source_ids is None:
            all_have_source_ids = False
            break
        resolved.append((typeid, source_ids[0], source_ids[1]))

    if all_have_source_ids and len(resolved) == len(attributes):
        out = bytearray()
        for serial_index, (typeid, siid, piid) in enumerate(resolved):
            out.extend((
                typeid & 0xFF,
                serial_index & 0xFF,
                siid & 0xFF,
                (siid >> 8) & 0xFF,
                (piid >> 8) & 0xFF,
                piid & 0xFF,
            ))
        return bytes(out)

    # 非米家/旧格式兼容：typeid + attrid + access_flag。
    access_flags = {"只读": 0x00, "读写": 0x01, "只写": 0x02}
    out = bytearray()
    for attrid_raw, meta in attributes:
        try:
            attrid = (
                int(str(attrid_raw), 16)
                if str(attrid_raw).lower().startswith("0x")
                else int(attrid_raw)
            )
        except (TypeError, ValueError):
            continue
        try:
            typeid = int(meta.get("typeid", 2))
        except (TypeError, ValueError):
            typeid = 2
        out.extend((
            typeid & 0xFF,
            attrid & 0xFF,
            access_flags.get(meta.get("access", "读写"), 0x01),
        ))
    return bytes(out)


def build_dev_info_data(cfg: dict, *, include_version_prefix: bool = True) -> bytes:
    """从产品配置构建 0x21 response 的 data 段。"""
    info = cfg.get("product_info") or {}
    prefix_enabled = bool(info.get("include_version_prefix", include_version_prefix))

    # 某些平台导出的 Base.expandRules 就是设备真实使用的完整扩展区。
    # 直接复用它可以保持 PID、Model 与 6 字节属性映射逐字节一致。
    expand_rules = _source_expand_rules(cfg)
    if expand_rules:
        return (_version_bytes(cfg) if prefix_enabled else b"") + expand_rules

    ext = bytearray()

    # PID: UINT32, attrid F7
    ext.extend((6, 0xF7))
    ext.extend(struct.pack(">I", _pid_to_uint32(info.get("pid"))))

    # Model: STRING, attrid F5, 2-byte length
    model_bytes = str(info.get("model") or "").encode("utf-8")
    ext.extend((11, 0xF5))
    ext.extend(len(model_bytes).to_bytes(2, "big"))
    ext.extend(model_bytes)

    # Attribute map: ARRAY, attrid F3, 2-byte length
    mapping = _build_attr_mapping(cfg)
    ext.extend((14, 0xF3))
    ext.extend(len(mapping).to_bytes(2, "big"))
    ext.extend(mapping)

    return (_version_bytes(cfg) if prefix_enabled else b"") + bytes(ext)


def encode_dev_info_frame(cfg: dict) -> bytes:
    data = build_dev_info_data(cfg)
    return encode_frame(0x21, cfg, direction="response", data=data)
