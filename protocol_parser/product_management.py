"""Pure helpers for listing and inspecting imported product JSON files."""
from __future__ import annotations

from collections import Counter
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


def disambiguate_product_catalog(
    products: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    """Make duplicate JSON product names unique by appending the filename."""
    json_names = [name for name, _, kind in products if kind == "json"]
    duplicate_names = {
        name for name, count in Counter(json_names).items() if count > 1
    }
    if not duplicate_names:
        return list(products)

    used_labels: set[str] = set()
    disambiguated: list[tuple[str, str, str]] = []
    for name, source, kind in products:
        if kind != "json" or name not in duplicate_names:
            label = name
        else:
            label = f"{name} ({Path(source).name})"
        if label in used_labels:
            stem = Path(source).stem
            suffix = 2
            while True:
                candidate = f"{name} ({stem}-{suffix}.json)"
                if candidate not in used_labels:
                    label = candidate
                    break
                suffix += 1
        used_labels.add(label)
        disambiguated.append((label, source, kind))
    return disambiguated


def product_record_display_label(
    record: ProductJsonRecord,
    name_counts: Mapping[str, int] | None = None,
) -> str:
    """Return a combo label; append filename when the product name repeats."""
    counts = name_counts or {}
    if counts.get(record.name, 0) > 1:
        filename = record.filename or record.source_path.name
        if filename:
            return f"{record.name} ({filename})"
    return record.name


def product_name_counts(records: list[ProductJsonRecord]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record.name] = counts.get(record.name, 0) + 1
    return counts
