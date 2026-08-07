"""V3.0 串口接入协议解析工具。

启动性能说明：
- 核心解析符号直接导出；
- CLI、日志监控等非 GUI 启动必需模块采用延迟导入；
- 避免打开图形界面时同步加载不使用的命令行模块。
"""
from __future__ import annotations

from .parser import (
    EncodeFrameError,
    FieldResult,
    Frame,
    ParseResult,
    ProtocolError,
    TYPEID_MAP,
    _log_error_to_disk,
    calc_checksum,
    classify_protocol_error,
    encode_frame,
    find_command,
    get_builtin_v3,
    load_protocol,
    merge_protocol,
    parse_data_fields,
    parse_frame,
    parse_hex_input,
    split_frame,
    to_hex,
)

from .app_info import APP_NAME, APP_VERSION

VERSION: str = APP_VERSION

_LAZY_EXPORTS = {
    "FrameSynchronizer": (".serial_collector", "FrameSynchronizer"),
    "SerialCollector": (".serial_collector", "SerialCollector"),
    "ResultLogger": (".monitor", "ResultLogger"),
    "run_paste_mode": (".monitor", "run_paste_mode"),
    "run_serial_mode": (".monitor", "run_serial_mode"),
    "list_serial_ports": (".monitor", "list_serial_ports"),
    "find_protocol_file": (".cli", "find_protocol_file"),
}


def __getattr__(name: str):
    """按需导入 GUI 启动阶段不需要的模块。"""
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    from importlib import import_module

    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(_LAZY_EXPORTS))


__all__ = [
    "VERSION",
    "APP_NAME",
    "EncodeFrameError",
    "FieldResult",
    "Frame",
    "FrameSynchronizer",
    "ParseResult",
    "ProtocolError",
    "ResultLogger",
    "SerialCollector",
    "TYPEID_MAP",
    "_log_error_to_disk",
    "calc_checksum",
    "classify_protocol_error",
    "encode_frame",
    "find_command",
    "find_protocol_file",
    "get_builtin_v3",
    "list_serial_ports",
    "load_protocol",
    "merge_protocol",
    "parse_data_fields",
    "parse_frame",
    "parse_hex_input",
    "run_paste_mode",
    "run_serial_mode",
    "split_frame",
    "to_hex",
]
