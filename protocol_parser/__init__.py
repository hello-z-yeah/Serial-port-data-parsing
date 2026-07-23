'''
Author: 侯泽钰 houzeyu@xiaojiang.cc
Date: 2026-07-20 12:36:35
LastEditors: 侯泽钰 houzeyu@xiaojiang.cc
LastEditTime: 2026-07-23 17:48:45
FilePath: \Serial-port-data-parsing\protocol_parser\__init__.py
Description: 这是默认设置,请设置`customMade`, 打开koroFileHeader查看配置 进行设置: https://github.com/OBKoro1/koro1FileHeader/wiki/%E9%85%8D%E7%BD%AE
'''
"""protocol_parser 包：V3.0 串口接入协议解析工具。"""
from .parser import (
    FieldResult,
    Frame,
    ParseResult,
    ProtocolError,
    TYPEID_MAP,
    calc_checksum,
    find_command,
    load_protocol,
    merge_protocol,
    parse_data_fields,
    parse_frame,
    parse_hex_input,
    split_frame,
    to_hex,
)
from .monitor import ResultLogger, run_paste_mode, run_serial_mode, list_serial_ports
from .serial_collector import FrameSynchronizer, SerialCollector

# 版本号（三位语义化）。发新版只改这里：主版本.次版本.修订号
VERSION: str = "1.0.1"
# 发布用 GitHub 仓库（owner/repo）
UPDATER_GITHUB_REPO: str = "hello-z-yeah/Serial-port-data-parsing"

__all__ = [
    "VERSION",
    "UPDATER_GITHUB_REPO",
    "FieldResult",
    "Frame",
    "FrameSynchronizer",
    "ParseResult",
    "ProtocolError",
    "ResultLogger",
    "SerialCollector",
    "TYPEID_MAP",
    "calc_checksum",
    "find_command",
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
