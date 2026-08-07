"""Pure helpers for listing and inspecting imported product JSON files."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class ProductJsonRecord:
    """One JSON product shown by the product-management dialog."""

    name: str
    source_path: Path
    filename: str
    pid: str = ""
    model: str = ""
    mcu_version: str = ""
    attribute_count: int = 0
    load_error: str = ""


def _format_version(value: object) -> str:
    if isinstance(value, (list, tuple)):
        return ".".join(str(part) for part in list(value)[:3])
    return str(value or "").strip()


def collect_product_json_records(
    product_sources: Mapping[str, object] | None,
    product_kinds: Mapping[str, object] | None,
) -> list[ProductJsonRecord]:
    """Return all imported JSON products without depending on the active product.

    Invalid or temporarily unreadable files are retained in the list with a
    ``load_error`` so the user can still select and delete the broken entry.
    """

    sources = product_sources or {}
    kinds = product_kinds or {}
    records: list[ProductJsonRecord] = []

    for raw_name, raw_source in sources.items():
        name = str(raw_name or "").strip()
        if not name or str(kinds.get(raw_name) or "").strip().lower() != "json":
            continue

        source_text = str(raw_source or "").strip()
        source_path = Path(source_text) if source_text else Path()
        pid = ""
        model = ""
        mcu_version = ""
        attribute_count = 0
        load_error = ""

        try:
            if not source_text:
                raise FileNotFoundError("产品来源路径为空")
            raw_cfg = json.loads(source_path.read_text(encoding="utf-8-sig"))
            if not isinstance(raw_cfg, dict):
                raise ValueError("产品 JSON 顶层必须是对象")
            info = raw_cfg.get("product_info")
            if not isinstance(info, dict):
                info = {}
            pid = str(info.get("pid") or "").strip()
            model = str(info.get("model") or "").strip()
            mcu_version = _format_version(info.get("mcu_version"))
            attrs = raw_cfg.get("attributes")
            if isinstance(attrs, dict):
                attribute_count = sum(
                    1 for key, value in attrs.items()
                    if not str(key).startswith("__") and isinstance(value, dict)
                )
        except Exception as exc:
            load_error = str(exc).strip() or exc.__class__.__name__

        records.append(
            ProductJsonRecord(
                name=name,
                source_path=source_path,
                filename=source_path.name if source_text else "",
                pid=pid,
                model=model,
                mcu_version=mcu_version,
                attribute_count=attribute_count,
                load_error=load_error,
            )
        )

    records.sort(key=lambda item: (item.name.casefold(), item.filename.casefold()))
    return records
