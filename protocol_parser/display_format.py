"""Thread-safe display string formatting for serial receive paths."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from protocol_parser.theme import PALETTE

ReceiveColorSegment = tuple[str, str | None, str | None, str | None]

_TS_OR_LEVEL_RE = re.compile(
    r"(\[\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?[ ]*\])|"
    r"(\[\s*(?:EMERG|ERROR|WARN|NOTICE|INFO|DEBUG|TRACE)\s*\])|"
    r"(\[(?:TX|RX)\])|"
    r"(Raw-(?:ASCII|HEX))",
    re.IGNORECASE,
)
LEVEL_STYLES = {
    "EMERG":  ("#C42B1C", "#FDE9E7"),
    "ERROR":  ("#C42B1C", "#FDE9E7"),
    "WARN":   ("#E65100", "#FFF3E0"),
    "NOTICE": ("#0066CC", "#E3F2FD"),
    "INFO":   ("#374151", "#E8E8E8"),
    "DEBUG":  ("#607D8B", "#ECEFF1"),
    "TRACE":  ("#607D8B", "#ECEFF1"),
}


def build_receive_color_segments(
    text: str,
    color: str | None = None,
) -> list[ReceiveColorSegment]:
    """Split one receive line into colored segments on the parse thread."""
    if not text:
        return []
    segments: list[ReceiveColorSegment] = []
    pos = 0
    for match in _TS_OR_LEVEL_RE.finditer(text):
        if match.start() > pos:
            segments.append((text[pos:match.start()], color, None, None))
        if match.group(1):
            segments.append((match.group(1), "#2E86FF", "#FFF9C4", None))
        elif match.group(2):
            level = match.group(2).strip("[] ").upper()
            fg, bg = LEVEL_STYLES.get(level, ("#374151", "#E8E8E8"))
            segments.append((match.group(2), fg, bg, None))
        elif match.group(3):
            tag = match.group(3).strip("[]").upper()
            if tag == "TX":
                segments.append((match.group(3), "#008000", "#E8F5E9", None))
            else:
                segments.append((match.group(3), "#0000CD", "#E3F2FD", None))
        else:
            segments.append((match.group(4), "#008000", "#E8F5E9", None))
        pos = match.end()
    if pos < len(text):
        segments.append((text[pos:], color, None, None))
    return segments


_MONITOR_DIRECTION_RE = re.compile(r"\[\s*(?:TX|RX)\s*\]", re.IGNORECASE)
_MONITOR_RAW_LABEL_RE = re.compile(r"Raw-(?:ASCII|HEX)\s*", re.IGNORECASE)


def clean_monitor_payload(text: str) -> str:
    """Remove direction/raw labels from monitor-page payload text."""
    cleaned = _MONITOR_DIRECTION_RE.sub("", str(text or ""))
    cleaned = _MONITOR_RAW_LABEL_RE.sub("", cleaned)
    return cleaned.strip()


def normalize_monitor_display_line(line: str) -> str:
    """Normalize one monitor realtime/record line to ``[ts] payload``."""
    raw = str(line or "").strip()
    if not raw:
        return ""
    match = re.match(
        r"^(\[\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?\])\s*(.*)$",
        raw,
    )
    if not match:
        cleaned = clean_monitor_payload(raw)
        return f"{cleaned}\n" if cleaned else ""
    ts_part, payload = match.groups()
    payload = clean_monitor_payload(payload)
    if not payload:
        return ""
    return f"{ts_part} {payload}\n"


def _clean_monitor_payload(text: str) -> str:
    return clean_monitor_payload(text)


def _monitor_payload_is_tx(data: bytes) -> bool:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return bool(re.search(r"\[\s*TX\s*\]", text, re.I))


def format_monitor_raw_line(
    data: bytes,
    ts: float,
    *,
    hex_format: bool,
) -> str:
    """Build one monitor-page RX line: ``[timestamp] payload`` without TX/RX/Raw tags."""
    if not hex_format and _monitor_payload_is_tx(data):
        return ""
    ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
    if hex_format:
        shown = " ".join(f"{byte:02X}" for byte in data)
    else:
        try:
            text = data.decode("utf-8")
            shown = (
                text.replace("\r\n", " ")
                .replace("\n", " ")
                .replace("\r", " ")
                .replace("\t", " ")
            )
            shown = _clean_monitor_payload(shown)
        except UnicodeDecodeError:
            shown = " ".join(f"{byte:02X}" for byte in data)
    if not shown:
        return ""
    return f"[{ts_str}] {shown}\n"


def format_receive_frame_line(
    result: Any,
    ts: float,
    *,
    hex_format: bool,
) -> tuple[str, str | None]:
    """Build one receive-page protocol frame line and its base color."""
    ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
    ok = result.error is None and result.checksum_ok is not False
    cs = "✓" if result.checksum_ok else ("✗" if result.checksum_ok is False else " ")
    status = "OK" if result.error is None else "ERR"
    raw_display = result.raw_hex
    if not hex_format:
        try:
            raw_bytes = bytes.fromhex(raw_display.replace(" ", ""))
            raw_display = "".join(chr(b) if 32 <= b < 127 else "." for b in raw_bytes)
        except Exception:
            pass

    line = f"[{ts_str}] {status} {cs} {result.cmd_code:<6} {result.cmd_name}"
    if result.direction:
        line += f" [{result.direction}]"
    data_fields = []
    in_data = False
    for field in result.fields:
        ftype = field.get("type", "")
        fname = field.get("name", "")
        ftext = field.get("text", "")
        if ftype == "separator":
            in_data = True
            continue
        if in_data and ftype not in ("header", "version", "cmd", "length", "checksum"):
            if isinstance(fname, str) and fname.startswith("attrid_"):
                continue
            if ftext:
                data_fields.append(f"{fname}={ftext}")
    if data_fields:
        line += f"  {{ {', '.join(data_fields)} }}"
    line += f"  | {raw_display}\n"
    color = "#0000CD" if ok else PALETTE["error"]
    return line, color


def format_receive_frame_item(
    result: Any,
    ts: float,
    *,
    hex_format: bool,
) -> dict[str, Any]:
    """Build one batched receive-page frame payload with pre-split color segments."""
    line, color = format_receive_frame_line(result, ts, hex_format=hex_format)
    return {
        "text": line,
        "color": color,
        "segments": build_receive_color_segments(line, color),
        "kind": "frame",
    }


def format_receive_raw_items(
    data: bytes,
    ts: float,
    *,
    hex_format: bool,
) -> list[dict[str, Any]]:
    """Build receive-page raw display items for one RX chunk."""
    ts_str = datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]
    monitor_line = format_monitor_raw_line(data, ts, hex_format=hex_format)
    if hex_format:
        shown = " ".join(f"{b:02X}" for b in data)
        line = f"[{ts_str}] [RX] Raw-HEX {shown}\n"
        return [{
            "text": line,
            "color": "#0000CD",
            "ts_colorize": False,
            "kind": "raw",
            "raw_bytes": data,
            "ts": ts,
            "monitor_line": monitor_line,
        }]

    text = data.decode("utf-8", errors="replace")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    items: list[dict[str, Any]] = []
    parts: list[str] = []
    for index, raw_line in enumerate(lines):
        if not raw_line and index != 0:
            continue
        printable = "".join(
            ch if (32 <= ord(ch) < 127 or ch == "\t") else "." for ch in raw_line
        )
        if index == 0:
            parts.append(f"[{ts_str}] [RX] Raw-ASCII {printable}\n")
        else:
            parts.append(f"{printable}\n")
    if parts:
        combined = "".join(parts)
        items.append({
            "text": combined,
            "color": "#0000CD",
            "segments": build_receive_color_segments(combined, "#0000CD"),
            "kind": "raw",
            "raw_bytes": data,
            "ts": ts,
            "monitor_line": monitor_line,
        })
    return items
