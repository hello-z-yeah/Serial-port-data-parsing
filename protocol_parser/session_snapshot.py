"""Session snapshot persistence.

The snapshot stores ordinary local preference/session state.  Writes are atomic
and durable; a corrupt file is preserved with a timestamped ``.corrupt`` suffix
for diagnostics instead of being deleted.
"""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any

from .exceptions import SnapshotError
from .paths import config_dir

SESSION_FILENAME = "session.json"


@dataclass
class SessionSnapshot:
    was_collecting: bool = False
    port: str = ""
    baudrate: int = 9600
    bytesize: int = 8
    stopbits: float = 1.0
    product_name: str = ""
    product_source: str = ""
    is_hex_format: bool = True
    direction: str = ""
    detail_mode: bool = False
    log_path: str = ""
    save_raw_enabled: bool = False
    save_raw_path: str = ""
    save_raw_filename: str = ""
    tx_send_mode: str = ""
    tx_cmd_code: str = ""
    tx_direction: str = ""
    tx_fields_json: str = ""
    tx_raw: str = ""
    tx_cycle_enabled: bool = False
    tx_interval_ms: int = 1000
    version: int = 2
    extras: dict[str, Any] = field(default_factory=dict)


def default_session_path() -> Path:
    return config_dir() / SESSION_FILENAME


def _fsync_directory(directory: Path) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save_snapshot(snap: SessionSnapshot, path: str | Path | None = None) -> Path:
    """Atomically persist a snapshot using same-directory temp + fsync + replace."""
    target = Path(path) if path else default_session_path()
    tmp = target.with_name(
        f".{target.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = asdict(snap)
        with open(tmp, "w", encoding="utf-8", newline="\n") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
            fp.write("\n")
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, target)
        _fsync_directory(target.parent)
        return target
    except Exception as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise SnapshotError(f"保存会话快照失败：{exc}") from exc


def _quarantine_corrupt(path: Path) -> Path | None:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    candidate = path.with_name(f"{path.stem}.corrupt.{stamp}{path.suffix}")
    try:
        os.replace(path, candidate)
        return candidate
    except OSError:
        return None


def load_snapshot(path: str | Path | None = None) -> SessionSnapshot | None:
    target = Path(path) if path is not None else default_session_path()
    if not target.exists():
        return None
    try:
        with open(target, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
    except (json.JSONDecodeError, UnicodeError, ValueError, TypeError):
        _quarantine_corrupt(target)
        return None
    except OSError as exc:
        raise SnapshotError(f"读取会话快照失败：{exc}") from exc

    if not isinstance(payload, dict):
        _quarantine_corrupt(target)
        return None

    snap = SessionSnapshot()
    allowed = {item.name for item in fields(SessionSnapshot)}
    defaults = SessionSnapshot()
    for key, value in payload.items():
        if key not in allowed:
            continue
        default = getattr(defaults, key)
        if isinstance(default, bool):
            if not isinstance(value, bool):
                continue
        elif isinstance(default, int) and not isinstance(default, bool):
            if isinstance(value, bool) or not isinstance(value, int):
                continue
        elif isinstance(default, float):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            value = float(value)
        elif isinstance(default, str):
            if not isinstance(value, str):
                continue
        elif isinstance(default, dict):
            if not isinstance(value, dict):
                continue
            value = dict(value)
        setattr(snap, key, value)

    return snap


def clear_snapshot(path: str | Path | None = None) -> None:
    target = Path(path) if path else default_session_path()
    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass


def snapshot_exists(path: str | Path | None = None) -> bool:
    target = Path(path) if path else default_session_path()
    try:
        return target.exists()
    except OSError:
        return False
