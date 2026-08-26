"""Parse action/event definitions from product function JSON."""
from __future__ import annotations

from typing import Any

from .product_importer import FORMAT_TO_TYPEID, _normalize_attr_key, _resolve_float_typeid, localized_attribute_name


def _coerce_typeid(raw: Any, default: int = 2) -> int:
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _first_text(item: dict, *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _parse_int_id(raw: Any) -> int | None:
    if raw in (None, ""):
        return None
    try:
        if isinstance(raw, str) and raw.lower().startswith("0x"):
            return int(raw, 16) & 0xFF
        return int(raw) & 0xFF
    except (TypeError, ValueError):
        return None


def _resolve_explicit_wire_id(item: dict) -> int | None:
    for key in ("serialId", "actionId", "eventId", "serial_id"):
        if key not in item or item.get(key) in (None, ""):
            continue
        parsed = _parse_int_id(item.get(key))
        if parsed is not None:
            return parsed
    return None


def _resolve_miot_action_event_id(item: dict, *, fallback: int = 0) -> int:
    """MIOT action/event 线协议下发 ID = iid + 20（与参考工具一致）。"""
    explicit = _resolve_explicit_wire_id(item)
    if explicit is not None:
        return explicit
    sub_id = int(item.get("iid", item.get("aiid", item.get("eiid", fallback))) or 0)
    return (sub_id + 20) & 0xFF


def _to_wise_wire_event_id(serial_id: int) -> int:
    """Wise ActionEvent 索引起始 id 需映射到线协议事件号（1→21, 2→22）。"""
    serial_id = int(serial_id) & 0xFF
    if serial_id < 0x10:
        return (serial_id + 20) & 0xFF
    return serial_id


def _resolve_kind(item: dict, *, default: str = "action") -> str:
    for key in ("type", "actionEventType", "entryType", "kind"):
        raw = str(item.get(key) or "").strip().lower()
        if not raw:
            continue
        if "event" in raw:
            return "event"
        if "action" in raw:
            return "action"
    urn = str(item.get("type") or "")
    if "event" in urn.lower():
        return "event"
    if "action" in urn.lower():
        return "action"
    return default


def _product_has_xjiang_data_service(data: dict) -> bool:
    """Detect xjiang data-service test products that must not mix in other services."""
    for service in data.get("services") or []:
        if not isinstance(service, dict):
            continue
        service_type = str(service.get("type") or "").lower()
        if "xjiang-spec" in service_type and ":service:data:" in service_type:
            return True
    return False


def _service_exports_action_events(service: dict, *, data: dict | None = None) -> bool:
    """Return True when a service block declares importable actions/events."""
    if not isinstance(service, dict):
        return False

    actions = [item for item in (service.get("actions") or []) if isinstance(item, dict)]
    events = [item for item in (service.get("events") or []) if isinstance(item, dict)]
    if not actions and not events:
        return False

    service_type = str(service.get("type") or "").lower()
    if "xjiang-spec" in service_type:
        return True
    for item in actions + events:
        if "xjiang-spec" in str(item.get("type") or "").lower():
            return True

    # xjiang 全类型测试 JSON 同时带 curtain 标准动作；只保留 data 服务，
    # 避免多个服务都使用 iid=1 时线协议 action id 21 冲突。
    if isinstance(data, dict) and _product_has_xjiang_data_service(data):
        return False

    return True


def _wire_attrid(siid: int, piid: int) -> int:
    return ((int(siid) * 16 + int(piid)) + 0x20) & 0xFF


def _lookup_property_meta(
    *,
    siid: int,
    piid: int,
    attributes: dict[str, dict],
    service: dict | None = None,
) -> dict:
    for meta in attributes.values():
        if not isinstance(meta, dict):
            continue
        if int(meta.get("source_siid", -1)) == int(siid) and int(meta.get("source_piid", -1)) == int(piid):
            return dict(meta)
    wire_id = _wire_attrid(siid, piid)
    attr_key = _normalize_attr_key(wire_id)
    meta = dict(attributes.get(attr_key) or {})
    if meta:
        return meta
    if isinstance(service, dict):
        for prop in service.get("properties") or []:
            if not isinstance(prop, dict):
                continue
            if int(prop.get("iid", prop.get("piid", -1)) or -1) != int(piid):
                continue
            name = _first_text(prop, "comment", "description", "name") or f"属性{piid}"
            fmt = str(prop.get("format") or "").lower()
            typeid = _resolve_float_typeid(prop)
            if typeid is None:
                typeid = FORMAT_TO_TYPEID.get(fmt, 2)
            return {
                "name": name,
                "cn_name": localized_attribute_name(name, fallback=name),
                "typeid": typeid,
                "initial_value": 0,
            }
    name = f"属性{piid}"
    return {"name": name, "cn_name": name, "typeid": 2, "initial_value": 0}


def _parse_param_list(
    raw_params: Any,
    attributes: dict[str, dict],
    *,
    siid: int = 0,
    service: dict | None = None,
) -> list[dict]:
    if raw_params in (None, ""):
        return []
    if isinstance(raw_params, dict):
        params = list(raw_params.values())
    elif isinstance(raw_params, list):
        params = raw_params
    else:
        return []

    parsed: list[dict] = []
    for index, item in enumerate(params):
        if isinstance(item, (int, float, str)) and not (isinstance(item, str) and item.strip() == ""):
            piid = _parse_int_id(item)
            if piid is None:
                continue
            meta = _lookup_property_meta(siid=siid, piid=piid, attributes=attributes, service=service)
            wire_id = _wire_attrid(siid, piid)
            parsed.append({
                "attrid": wire_id,
                "piid": int(piid) & 0xFF,
                "name": str(meta.get("name") or f"参数{piid}"),
                "cn_name": str(meta.get("cn_name") or meta.get("name") or f"参数{piid}"),
                "typeid": _coerce_typeid(meta.get("typeid"), 2),
                "default": meta.get("initial_value", 0),
            })
            continue
        if not isinstance(item, dict):
            continue
        raw_id = item.get("serialId", item.get("attrid", item.get("id", item.get("iid", index))))
        piid = _parse_int_id(raw_id)
        if piid is None:
            piid = index
        if siid:
            attrid = _wire_attrid(siid, piid)
        else:
            attrid = piid
        attr_key = _normalize_attr_key(attrid)
        attr_meta = attributes.get(attr_key, {})
        try:
            typeid = _coerce_typeid(item.get("type", item.get("typeid", attr_meta.get("typeid"))), 2)
        except (TypeError, ValueError):
            typeid = _coerce_typeid(attr_meta.get("typeid"), 2)
        name = _first_text(
            item,
            "name",
            "attributeName",
            "attributeKey",
            "propertyName",
            "description",
            "comment",
        ) or str(attr_meta.get("cn_name") or attr_meta.get("name") or f"参数{index}")
        cn_name = localized_attribute_name(name, fallback=name)
        default_value = item.get("value", item.get("default", item.get("nowValue", attr_meta.get("initial_value", 0))))
        parsed.append({
            "attrid": int(attrid) & 0xFF,
            "piid": int(piid) & 0xFF,
            "name": name,
            "cn_name": cn_name,
            "typeid": typeid,
            "default": default_value,
        })
    return parsed


def _normalize_action_event_entry(
    item: dict,
    *,
    attributes: dict[str, dict],
    service: dict | None = None,
    service_name: str = "",
    default_kind: str = "action",
) -> dict:
    kind = _resolve_kind(item, default=default_kind)
    siid = int((service or {}).get("iid", (service or {}).get("siid", 0)) or 0)
    service_iid = int(item.get("iid", item.get("aiid", item.get("eiid", 0))) or 0)
    explicit = _resolve_explicit_wire_id(item)
    if explicit is not None:
        serial_id = explicit
    elif siid:
        serial_id = _resolve_miot_action_event_id(item, fallback=service_iid)
    else:
        serial_id = int(service_iid or explicit or 0) & 0xFF
    name = _first_text(
        item,
        "name",
        "actionName",
        "eventName",
        "attributeName",
        "description",
    ) or f"{kind}-{serial_id:02X}"
    cn_name = localized_attribute_name(
        _first_text(item, "comment", "cn_name", "displayName") or name,
        fallback=name,
    )
    in_params = _parse_param_list(
        item.get("inParams")
        or item.get("inputParams")
        or item.get("inputs")
        or item.get("in")
        or item.get("input"),
        attributes,
        siid=siid,
        service=service,
    )
    out_params = _parse_param_list(
        item.get("outParams")
        or item.get("outputParams")
        or item.get("outputs")
        or item.get("out")
        or item.get("output")
        or item.get("arguments"),
        attributes,
        siid=siid,
        service=service,
    )
    return {
        "serial_id": serial_id,
        "service_siid": siid,
        "service_iid": service_iid,
        "service": str(item.get("service") or item.get("serviceName") or service_name or "").strip(),
        "kind": kind,
        "name": name,
        "cn_name": cn_name,
        "in_params": in_params,
        "out_params": out_params,
    }


def parse_action_event_entries(data: dict | list, attributes: dict[str, dict]) -> tuple[list[dict], list[dict]]:
    """Return normalized (actions, events) lists from function JSON data."""
    actions: list[dict] = []
    events: list[dict] = []
    seen: set[tuple[str, int, int]] = set()

    def add_entry(entry: dict) -> None:
        kind = str(entry.get("kind") or "action")
        siid = int(entry.get("service_siid") or 0)
        sub_id = int(entry.get("service_iid") or entry.get("serial_id") or 0)
        key = (kind, siid, sub_id)
        if key in seen:
            return
        seen.add(key)
        if kind == "event":
            events.append(entry)
        else:
            actions.append(entry)

    if isinstance(data, dict):
        raw_items = (
            data.get("ActionEvent")
            or data.get("actionEvent")
            or data.get("actions_events")
            or []
        )
        if isinstance(raw_items, dict):
            raw_items = list(raw_items.values())
        if isinstance(raw_items, list):
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                entry = _normalize_action_event_entry(item, attributes=attributes)
                raw_serial = _resolve_explicit_wire_id(item)
                if raw_serial is None:
                    raw_serial = int(entry.get("service_iid") or entry.get("serial_id") or 0) & 0xFF
                entry["service_siid"] = 0
                entry["service_iid"] = int(raw_serial) & 0xFF
                if entry.get("kind") == "event":
                    entry["serial_id"] = _to_wise_wire_event_id(raw_serial)
                else:
                    entry["serial_id"] = int(raw_serial) & 0xFF
                add_entry(entry)

        for service in data.get("services") or []:
            if not isinstance(service, dict):
                continue
            if not _service_exports_action_events(service, data=data):
                continue
            siid = int(service.get("iid", service.get("siid", 0)) or 0)
            service_name = str(service.get("comment") or service.get("description") or service.get("name") or f"服务{siid}")
            for action in service.get("actions") or []:
                if isinstance(action, dict):
                    entry = _normalize_action_event_entry(
                        action,
                        attributes=attributes,
                        service=service,
                        service_name=service_name,
                        default_kind="action",
                    )
                    entry["serial_id"] = _resolve_miot_action_event_id(action, fallback=entry["service_iid"])
                    add_entry(entry)
            for event in service.get("events") or []:
                if isinstance(event, dict):
                    entry = _normalize_action_event_entry(
                        event,
                        attributes=attributes,
                        service=service,
                        service_name=service_name,
                        default_kind="event",
                    )
                    entry["serial_id"] = _resolve_miot_action_event_id(event, fallback=entry["service_iid"])
                    add_entry(entry)

    actions.sort(key=lambda item: (int(item.get("service_siid") or 0), int(item.get("service_iid") or 0)))
    events.sort(key=lambda item: (int(item.get("service_siid") or 0), int(item.get("service_iid") or 0)))
    return actions, events


def format_param_summary(params: list[dict]) -> str:
    if not params:
        return "—"
    parts: list[str] = []
    for index, param in enumerate(params):
        attrid = int(param.get("attrid") or 0) & 0xFF
        typeid = _coerce_typeid(param.get("typeid"), 2)
        label = str(param.get("cn_name") or param.get("name") or f"参数{index}")
        parts.append(f"[{index}] {label} (0x{attrid:02X}, type {typeid})")
    return "\n".join(parts)


def action_event_entry_key(entry: dict) -> tuple[int, int, str]:
    return (
        int(entry.get("service_siid") or 0),
        int(entry.get("service_iid") or 0),
        str(entry.get("kind") or "action"),
    )


def lookup_action_event_label(cfg: dict | None, kind: str, wire_id: int) -> str:
    """按线协议 id 查找已导入动作/事件的中文名。"""
    if not isinstance(cfg, dict):
        return ""
    bucket = "events" if str(kind or "").lower() == "event" else "actions"
    wire_id = int(wire_id) & 0xFF
    for item in cfg.get(bucket) or []:
        if not isinstance(item, dict):
            continue
        try:
            serial_id = int(item.get("serial_id", -1)) & 0xFF
        except (TypeError, ValueError):
            continue
        if serial_id == wire_id:
            return str(item.get("cn_name") or item.get("name") or "").strip()
    return ""
