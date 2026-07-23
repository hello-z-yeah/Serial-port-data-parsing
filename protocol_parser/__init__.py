'''
Author: 侯泽钰 houzeyu@xiaojiang.cc
Date: 2026-07-20 12:36:35
LastEditors: 侯泽钰 houzeyu@xiaojiang.cc
LastEditTime: 2026-07-23 21:15:36
FilePath: \Serial-port-data-parsing\protocol_parser\__init__.py
Description: 这是默认设置,请设置`customMade`, 打开koroFileHeader查看配置 进行设置: https://github.com/OBKoro1/koro1FileHeader/wiki/%E9%85%8D%E7%BD%AE
'''
"""protocol_parser 包：V3.0 串口接入协议解析工具。

注意导入顺序：cli.py / monitor.py / serial_collector.py 都可能依赖 parser.py，
因此必须先把 parser 的符号全部导入完后，再引入 cli/monitor/serial_collector，
避免 circular import 或 ImportError: cannot import name 'classify_protocol_error'。
"""
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
from .serial_collector import FrameSynchronizer, SerialCollector
from .monitor import ResultLogger, run_paste_mode, run_serial_mode, list_serial_ports
from .cli import find_protocol_file

# 版本号（三位语义化）。发新版只改这里：主版本.次版本.修订号
VERSION: str = "1.0.1"
# 发布用 GitHub 仓库（owner/repo）
UPDATER_GITHUB_REPO: str = "hello-z-yeah/Serial-port-data-parsing"

__all__ = [
    "VERSION",
    "UPDATER_GITHUB_REPO",
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
