"""Validated persistence for the monitor page preferences."""
from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

from .paths import config_dir

MONITOR_SETTINGS_FILENAME = "monitor_settings.json"
DEFAULT_MAX_RECORD_LINES = 5000
MAX_MONITOR_PATTERN_CHARS = 4096


def default_monitor_settings_path() -> Path:
    return config_dir() / MONITOR_SETTINGS_FILENAME


def sanitize_monitor_settings(payload: Any) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    pattern_text = data.get("pattern_text", "")
    if not isinstance(pattern_text, str):
        pattern_text = ""
    pattern_text = pattern_text[:MAX_MONITOR_PATTERN_CHARS]
    max_record_lines = data.get("max_record_lines", DEFAULT_MAX_RECORD_LINES)
    if isinstance(max_record_lines, bool) or not isinstance(max_record_lines, int):
        max_record_lines = DEFAULT_MAX_RECORD_LINES
    max_record_lines = min(100_000, max(100, max_record_lines))
    return {
        "pattern_text": pattern_text,
        "is_hex": bool(data.get("is_hex", False)),
        "hex_format": bool(data.get("hex_format", False)),
        "autoscroll": bool(data.get("autoscroll", True)),
        "max_record_lines": max_record_lines,
    }


def load_monitor_settings(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path is not None else default_monitor_settings_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError, TypeError, ValueError):
        return sanitize_monitor_settings({})
    return sanitize_monitor_settings(payload)


def save_monitor_settings(
    payload: dict[str, Any],
    path: str | Path | None = None,
) -> Path:
    target = Path(path) if path is not None else default_monitor_settings_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(
                sanitize_monitor_settings(payload),
                stream,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        return target
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
