"""V3.0 串口接入协议解析器核心模块。

支持：
- 二进制定长帧头 + 变长 Data
- 嵌套属性块解析（typeid + attrid + [len] + value）
- 产品属性表查询（attrid → 名称/类型/取值说明）
- typeid 类型映射表
- 错误码、状态码枚举映射
"""
from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class ProtocolError(Exception):
    """协议解析相关错误。"""


# ---------- V3.0 typeid 类型表 ----------

# 协议文档表 7 定义的 typeid
TYPEID_MAP = {
    0:  {"name": "BOOL",    "size": 1, "ctype": "uint8",   "fmt": ">B"},
    1:  {"name": "INT8",    "size": 1, "ctype": "int8",    "fmt": ">b"},
    2:  {"name": "UINT8",   "size": 1, "ctype": "uint8",   "fmt": ">B"},
    3:  {"name": "INT16",   "size": 2, "ctype": "int16_be",  "fmt": ">h"},
    4:  {"name": "UINT16",  "size": 2, "ctype": "uint16_be", "fmt": ">H"},
    5:  {"name": "INT32",   "size": 4, "ctype": "int32_be",  "fmt": ">i"},
    6:  {"name": "UINT32",  "size": 4, "ctype": "uint32_be", "fmt": ">I"},
    7:  {"name": "INT64",   "size": 8, "ctype": "int64_be",  "fmt": ">q"},
    8:  {"name": "UINT64",  "size": 8, "ctype": "uint64_be", "fmt": ">Q"},
    9:  {"name": "FLOAT32", "size": 4, "ctype": "float32_be", "fmt": ">f"},
    10: {"name": "FLOAT64", "size": 8, "ctype": "float64_be", "fmt": ">d"},
    11: {"name": "STRING",  "size": None, "ctype": "string"},
    12: {"name": "DATE",    "size": None, "ctype": "date"},
    13: {"name": "STRUCT",  "size": None, "ctype": "struct"},
    14: {"name": "ARRAY",   "size": None, "ctype": "array"},
    15: {"name": "F1_U16",  "size": 2, "ctype": "uint16_be", "fmt": ">H", "scale": 0.1},
    16: {"name": "F2_U16",  "size": 2, "ctype": "uint16_be", "fmt": ">H", "scale": 0.01},
    17: {"name": "F1_U32",  "size": 4, "ctype": "uint32_be", "fmt": ">I", "scale": 0.1},
    18: {"name": "F2_U32",  "size": 4, "ctype": "uint32_be", "fmt": ">I", "scale": 0.01},
    19: {"name": "F1_I16",  "size": 2, "ctype": "int16_be",  "fmt": ">h", "scale": 0.1},
    20: {"name": "F2_I16",  "size": 2, "ctype": "int16_be",  "fmt": ">h", "scale": 0.01},
    21: {"name": "F1_I32",  "size": 4, "ctype": "int32_be",  "fmt": ">i", "scale": 0.1},
    22: {"name": "F2_I32",  "size": 4, "ctype": "int32_be",  "fmt": ">i", "scale": 0.01},
    23: {"name": "GROUP",   "size": None, "ctype": "group"},
    24: {"name": "STRING_ARRAY", "size": None, "ctype": "string_array"},
}

# 强制上报标志位
TYPEID_FORCE_REPORT_BIT = 0x80


# ---------- 配置加载 ----------

def load_protocol(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise ProtocolError(f"协议配置文件不存在: {p}")
    with p.open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    _validate_protocol(cfg)
    return cfg


def _validate_protocol(cfg: dict) -> None:
    if "product" not in cfg:
        raise ProtocolError("协议配置缺少 'product' 字段")
    if "commands" not in cfg or not isinstance(cfg["commands"], list):
        raise ProtocolError("协议配置缺少 'commands' 列表")


# ---------- 字节工具 ----------

def parse_hex_input(text: str) -> bytes:
    """把用户输入的 hex 字符串解析为 bytes。"""
    s = text.strip()
    if not s:
        raise ProtocolError("输入为空")
    s = s.replace("0x", "").replace("0X", "")
    for sep in [",", " ", "\t", "\n", ";"]:
        s = s.replace(sep, "")
    if len(s) % 2 != 0:
        raise ProtocolError(f"hex 长度为奇数，无法配对: {s}")
    try:
        return bytes.fromhex(s)
    except ValueError as e:
        raise ProtocolError(f"hex 解析失败: {e}") from e


def to_hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


# ---------- 校验算法 ----------

def calc_checksum(data: bytes, algorithm: str) -> bytes:
    algorithm = algorithm.lower()
    if algorithm == "sum":
        return bytes([sum(data) & 0xFF])
    if algorithm == "xor":
        v = 0
        for b in data:
            v ^= b
        return bytes([v & 0xFF])
    if algorithm == "crc8":
        crc = 0xFF
        for b in data:
            crc ^= b
            for _ in range(8):
                crc = (crc << 1) ^ 0x07 if crc & 0x80 else crc << 1
                crc &= 0xFF
        return bytes([crc])
    raise ProtocolError(f"不支持的校验算法: {algorithm}")


# ---------- 帧拆分 ----------

@dataclass
class Frame:
    raw: bytes
    header: int
    ver: int
    cmd_code: int
    length: int
    data: bytes
    checksum_ok: bool | None
    checksum_expected: bytes | None
    checksum_actual: bytes | None


def split_frame(data: bytes, cfg: dict) -> Frame:
    """按 V3.0 帧结构拆分。

    帧结构: Header(2B, 0xA5A5) + Ver(1B, 0x03) + Cmd(1B) + Length(2B 大端) + Data(nB) + CHK(1B, sum%256)
    """
    frame_cfg = cfg.get("frame", {})

    # 帧头
    header_size = frame_cfg.get("header_size", 2)
    if len(data) < header_size:
        raise ProtocolError(f"数据过短 ({len(data)}B)，无法读取帧头")
    header = int.from_bytes(data[:header_size], "big")
    expected_header = _parse_int(frame_cfg.get("header", "0xA5A5"))
    if header != expected_header:
        raise ProtocolError(
            f"帧头不匹配: 期望 0x{expected_header:04X}, 实际 0x{header:04X}"
        )

    # 版本
    ver_offset = frame_cfg.get("ver_offset", 2)
    ver_size = frame_cfg.get("ver_size", 1)
    ver = int.from_bytes(data[ver_offset:ver_offset + ver_size], "big")
    expected_ver = frame_cfg.get("ver")
    if expected_ver is not None and ver != _parse_int(expected_ver):
        raise ProtocolError(f"版本不匹配: 期望 {_parse_int(expected_ver)}, 实际 {ver}")

    # 命令字
    cmd_offset = frame_cfg.get("cmd_offset", 3)
    cmd_code = data[cmd_offset]

    # 数据长度
    length_offset = frame_cfg.get("length_offset", 4)
    length_size = frame_cfg.get("length_size", 2)
    length_byte_order = frame_cfg.get("length_byte_order", "big")
    length = int.from_bytes(
        data[length_offset:length_offset + length_size],
        byteorder=length_byte_order,
    )

    # 校验
    checksum_cfg = frame_cfg.get("checksum")
    checksum_ok: bool | None = None
    checksum_expected: bytes | None = None
    checksum_actual: bytes | None = None
    data_end = len(data)

    if checksum_cfg:
        cs_len = checksum_cfg.get("length", 1)
        cs_algo = checksum_cfg.get("algorithm", "sum")
        covers = checksum_cfg.get("covers", "from_start_to_checksum_exclusive")
        checksum_expected = data[-cs_len:]
        data_end = len(data) - cs_len

        if covers == "from_start_to_checksum_exclusive":
            covered = data[:data_end]
        elif covers == "from_cmd_to_checksum_exclusive":
            covered = data[cmd_offset:data_end]
        else:
            raise ProtocolError(f"不支持的 covers: {covers}")

        checksum_actual = calc_checksum(covered, cs_algo)
        checksum_ok = checksum_expected == checksum_actual

    # Data 区域：优先使用 length 字段截取（这样即使校验缺失也能解析）
    data_start = length_offset + length_size
    # data_end_by_length = data_start + length
    # data_end_by_actual = len(data) - cs_len (if checksum)
    payload = data[data_start:data_start + length]
    # 如果按 length 截取的字节数不足（输入被截断），则用实际剩余字节
    if len(payload) < length:
        # 输入数据被截断，使用实际可用的字节
        payload = data[data_start:data_end]

    return Frame(
        raw=data,
        header=header,
        ver=ver,
        cmd_code=cmd_code,
        length=length,
        data=payload,
        checksum_ok=checksum_ok,
        checksum_expected=checksum_expected,
        checksum_actual=checksum_actual,
    )


# ---------- 命令查找 ----------

def find_command(cfg: dict, cmd_code: int) -> dict | None:
    """查找命令定义。支持两种结构：
    1. 旧版：cmd 自身包含 data 字段（定长）
    2. V3.0：cmd 包含 request/response 两个子对象，每个含自己的 data
    """
    for cmd in cfg["commands"]:
        if _parse_int(cmd["cmd_code"]) == cmd_code:
            return cmd
    return None


def _try_parse_direction(data: bytes, data_def: dict, cfg: dict) -> tuple[list[FieldResult], bool]:
    """尝试一个方向的解析，返回 (结果, 是否无错误)。"""
    try:
        results = parse_data_fields(data, data_def, cfg)
        has_error = any(r.type == "error" for r in results)
        return results, not has_error
    except ProtocolError:
        return [], False


# ---------- Data 解析 ----------

@dataclass
class FieldResult:
    name: str
    type: str
    value: Any
    text: str
    offset: int = 0
    length: int = 0
    children: list[dict] = field(default_factory=list)
    raw: bytes | None = None

    def to_dict(self) -> dict:
        d = {
            "name": self.name,
            "type": self.type,
            "value": self.value,
            "text": self.text,
        }
        if self.offset:
            d["offset"] = self.offset
        if self.length:
            d["length"] = self.length
        if self.children:
            d["children"] = self.children
        if self.raw is not None:
            d["raw"] = self.raw.hex().upper()
        return d


def parse_data_fields(data: bytes, data_def: dict, cfg: dict) -> list[FieldResult]:
    """按命令的 data 定义解析 Data 区域。

    支持两种模式：
    1. fields: 定长字段列表（同原协议）
    2. format: 特殊格式（如 attr_list, attr_unit, firmware, time 等）
    """
    results: list[FieldResult] = []
    fmt = data_def.get("format")
    fields_def = data_def.get("fields")

    if fields_def:
        for fdef in fields_def:
            try:
                results.append(_parse_fixed_field(data, fdef, cfg))
            except ProtocolError as e:
                results.append(FieldResult(
                    name=fdef.get("name", "?"),
                    type=fdef.get("type", ""),
                    value=None,
                    text=f"解析失败: {e}",
                ))
        return results

    if fmt:
        return _parse_format(data, fmt, data_def, cfg)

    # 默认：当 raw 处理
    if data:
        results.append(FieldResult(
            name="Data",
            type="raw",
            value=to_hex(data),
            text=to_hex(data),
            length=len(data),
            raw=data,
        ))
    return results


def _parse_fixed_field(data: bytes, fdef: dict, cfg: dict) -> FieldResult:
    offset = fdef["offset"]
    length = fdef.get("length", 1)
    ftype = fdef.get("type", "hex")
    chunk = data[offset:offset + length]
    if len(chunk) < length:
        raise ProtocolError(
            f"字段 '{fdef['name']}' 越界: offset={offset}, length={length}, 帧总长 {len(data)}"
        )

    value, text = _decode_chunk(chunk, ftype, fdef, cfg)

    # 缩放
    if "scale" in fdef and isinstance(value, (int, float)):
        scaled = value * fdef["scale"]
        text = _format_value(scaled)
        value = scaled

    # 单位
    unit = fdef.get("unit")
    if unit and isinstance(value, (int, float)):
        text = f"{text} {unit}"

    # 期望值
    if "expected" in fdef:
        exp = fdef["expected"]
        exp_val = _parse_int(exp) if isinstance(exp, str) else exp
        if value != exp_val:
            text = f"{text} (期望 {exp})"

    return FieldResult(
        name=fdef.get("name", "?"),
        type=ftype,
        value=value,
        text=text,
        offset=offset,
        length=length,
        raw=chunk,
    )


def _parse_format(data: bytes, fmt: str, data_def: dict, cfg: dict) -> list[FieldResult]:
    """按格式名解析 Data 块。"""
    if fmt == "attr_list":
        # 多个属性值拼接：循环解析 typeid + attrid + [len] + value
        return _parse_attr_list(data, cfg, force_report=data_def.get("force_report", True))
    if fmt == "attr_unit":
        # 属性 id 单元：每字节一个 attrid
        return _parse_attr_unit(data, cfg)
    if fmt == "msg_id_then_attr_unit":
        # 消息 id + 属性 id 单元（每字节一个 attrid）
        if not data:
            raise ProtocolError("Data 为空")
        msg_id = data[0]
        results = [FieldResult(
            name="消息id", type="uint8", value=msg_id, text=str(msg_id),
            offset=0, length=1, raw=data[:1],
        )]
        results.extend(_parse_attr_unit(data[1:], cfg))
        return results
    if fmt == "msg_id_then_attr":
        # 消息 id（1B） + 属性单元
        if not data:
            raise ProtocolError("Data 为空，无法读取消息 id")
        msg_id = data[0]
        results = [FieldResult(
            name="消息id",
            type="uint8",
            value=msg_id,
            text=str(msg_id),
            offset=0,
            length=1,
            raw=data[:1],
        )]
        results.extend(_parse_attr_list(data[1:], cfg, force_report=True))
        return results
    if fmt == "msg_id":
        # 仅消息 id
        if not data:
            raise ProtocolError("Data 为空")
        msg_id = data[0]
        return [FieldResult(
            name="消息id",
            type="uint8",
            value=msg_id,
            text=str(msg_id),
            offset=0,
            length=1,
            raw=data[:1],
        )]
    if fmt == "msg_id_then_action":
        # 消息 id + 行为 id + 行为参数
        if len(data) < 2:
            raise ProtocolError("Data 过短")
        msg_id = data[0]
        action_id = data[1]
        results = [
            FieldResult(
                name="消息id", type="uint8", value=msg_id, text=str(msg_id),
                offset=0, length=1, raw=data[:1],
            ),
            FieldResult(
                name="行为 Action ID", type="uint8", value=action_id,
                text=str(action_id), offset=1, length=1, raw=data[1:2],
            ),
        ]
        if len(data) > 2:
            results.extend(_parse_attr_list(data[2:], cfg, force_report=True))
        return results
    if fmt == "event":
        # 事件 id + 事件参数
        if not data:
            raise ProtocolError("Data 为空")
        event_id = data[0]
        results = [FieldResult(
            name="事件 Event ID", type="uint8", value=event_id,
            text=str(event_id), offset=0, length=1, raw=data[:1],
        )]
        if len(data) > 1:
            results.extend(_parse_attr_list(data[1:], cfg, force_report=True))
        return results
    if fmt == "errcode":
        if not data:
            raise ProtocolError("Data 为空")
        err = data[0]
        err_map = cfg.get("enums", {}).get("errcode", {})
        text = err_map.get(str(err), f"未知({err})")
        return [FieldResult(
            name="错误码", type="uint8", value=err, text=text,
            offset=0, length=1, raw=data[:1],
        )]
    if fmt == "errcode_then_attr":
        # 错误码 + 属性列表
        if not data:
            raise ProtocolError("Data 为空")
        err = data[0]
        err_map = cfg.get("enums", {}).get("errcode", {})
        text = err_map.get(str(err), f"未知({err})")
        results = [FieldResult(
            name="错误码", type="uint8", value=err, text=text,
            offset=0, length=1, raw=data[:1],
        )]
        results.extend(_parse_attr_list(data[1:], cfg, force_report=True))
        return results
    if fmt == "errcode_then_partition":
        # 错误码 + 分区序号(2B 大端) + 升级包序号(1B)
        if len(data) < 4:
            raise ProtocolError("Data 过短")
        err = data[0]
        err_map = cfg.get("enums", {}).get("errcode", {})
        err_text = err_map.get(str(err), f"未知({err})")
        partition = int.from_bytes(data[1:3], "big")
        pkg = data[3]
        return [
            FieldResult("错误码", "uint8", err, err_text, 0, 1, raw=data[:1]),
            FieldResult("分区序号", "uint16_be", partition, str(partition), 1, 2, raw=data[1:3]),
            FieldResult("升级包序号", "uint8", pkg, str(pkg), 3, 1, raw=data[3:4]),
        ]
    if fmt == "partition_pkg":
        # 分区序号(2B 大端) + 升级包序号(1B) + 升级数据(n)
        if len(data) < 3:
            raise ProtocolError("Data 过短")
        partition = int.from_bytes(data[:2], "big")
        pkg = data[2]
        fw_data = data[3:]
        results = [
            FieldResult("分区序号", "uint16_be", partition, str(partition), 0, 2, raw=data[:2]),
            FieldResult("升级包序号", "uint8", pkg, str(pkg), 2, 1, raw=data[2:3]),
            FieldResult("升级数据", "raw", to_hex(fw_data), f"{len(fw_data)} 字节", 3, len(fw_data), raw=fw_data),
        ]
        return results
    if fmt == "ota_crc":
        # 分区序号(2B) + CRC32(4B)
        if len(data) < 6:
            raise ProtocolError("Data 过短")
        partition = int.from_bytes(data[:2], "big")
        crc = int.from_bytes(data[2:6], "big")
        return [
            FieldResult("分区序号", "uint16_be", partition, str(partition), 0, 2, raw=data[:2]),
            FieldResult("CRC32", "uint32_be", crc, f"0x{crc:08X}", 2, 4, raw=data[2:6]),
        ]
    if fmt == "partition_then_attr":
        # 分区序号(2B) + CRC32(4B)
        if len(data) < 6:
            raise ProtocolError("Data 过短")
        partition = int.from_bytes(data[:2], "big")
        crc = int.from_bytes(data[2:6], "big")
        return [
            FieldResult("分区序号", "uint16_be", partition, str(partition), 0, 2, raw=data[:2]),
            FieldResult("CRC32", "uint32_be", crc, f"0x{crc:08X}", 2, 4, raw=data[2:6]),
        ]
    if fmt == "dev_version":
        # 设备版本 3B（主.次.修正） + 扩展信息
        if len(data) < 3:
            raise ProtocolError("Data 过短")
        major, minor, patch = data[0], data[1], data[2]
        ver_text = f"{major}.{minor}.{patch}"
        results = [FieldResult(
            "设备版本", "version3", (major, minor, patch),
            ver_text, 0, 3, raw=data[:3],
        )]
        if len(data) > 3:
            ext = data[3:]
            results.append(FieldResult(
                "扩展信息", "raw", to_hex(ext), to_hex(ext),
                3, len(ext), raw=ext,
            ))
        return results
    if fmt == "net_config":
        # 配网方式
        if not data:
            raise ProtocolError("Data 为空")
        v = data[0]
        net_map = cfg.get("enums", {}).get("net_config_type", {})
        text = net_map.get(str(v), f"未知({v})")
        return [FieldResult("配网方式", "uint8", v, text, 0, 1, raw=data[:1])]
    if fmt == "module_status":
        # 模组工作状态
        if not data:
            raise ProtocolError("Data 为空")
        v = data[0]
        status_map = cfg.get("enums", {}).get("module_status", {})
        text = status_map.get(str(v), f"未知({v})")
        return [FieldResult("模组工作状态", "uint8", v, text, 0, 1, raw=data[:1])]
    if fmt == "heartbeat_resp":
        # 心跳响应: 0=重启后第一次 1=正常 2=正在升级
        if not data:
            raise ProtocolError("Data 为空")
        v = data[0]
        hb_map = cfg.get("enums", {}).get("heartbeat_resp", {})
        text = hb_map.get(str(v), f"未知({v})")
        return [FieldResult("MCU心跳值", "uint8", v, text, 0, 1, raw=data[:1])]
    if fmt == "get_time":
        # 时区(1B)
        if not data:
            raise ProtocolError("Data 为空")
        tz = data[0]
        # 处理负数
        tz_val = tz if tz < 128 else tz - 256
        return [FieldResult("时区", "int8", tz_val, f"UTC{'+' if tz_val >= 0 else ''}{tz_val}", 0, 1, raw=data[:1])]
    if fmt == "get_time_resp":
        # 错误码(1B) + 时区(1B) + 年(1B, 0=2000) + 月(1B) + 日(1B) + 星期(1B) + 时(1B) + 分(1B) + 秒(1B)
        if len(data) < 9:
            raise ProtocolError("Data 长度不足 9 字节")
        err = data[0]
        tz = data[1]
        tz_val = tz if tz < 128 else tz - 256
        year = 2000 + data[2]
        month = data[3]
        day = data[4]
        weekday = data[5]
        hour = data[6]
        minute = data[7]
        second = data[8]
        weekday_names = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        err_map = cfg.get("enums", {}).get("errcode", {})
        return [
            FieldResult("错误码", "uint8", err, err_map.get(str(err), f"未知({err})"), 0, 1, raw=data[:1]),
            FieldResult("时区", "int8", tz_val, f"UTC{'+' if tz_val >= 0 else ''}{tz_val}", 1, 1, raw=data[1:2]),
            FieldResult("年", "uint8", year, str(year), 2, 1, raw=data[2:3]),
            FieldResult("月", "uint8", month, str(month), 3, 1, raw=data[3:4]),
            FieldResult("日", "uint8", day, str(day), 4, 1, raw=data[4:5]),
            FieldResult("星期", "uint8", weekday, weekday_names[weekday] if 0 < weekday < 8 else str(weekday), 5, 1, raw=data[5:6]),
            FieldResult("时", "uint8", hour, str(hour), 6, 1, raw=data[6:7]),
            FieldResult("分", "uint8", minute, str(minute), 7, 1, raw=data[7:8]),
            FieldResult("秒", "uint8", second, str(second), 8, 1, raw=data[8:9]),
        ]
    if fmt == "service_set":
        # 服务数据：属性列表（attrid + value）
        return _parse_attr_list(data, cfg, force_report=True)
    if fmt == "product_test":
        # 产测状态
        if not data:
            raise ProtocolError("Data 为空")
        v = data[0]
        prod_map = cfg.get("enums", {}).get("product_test_status", {})
        text = prod_map.get(str(v), f"未知({v})")
        return [FieldResult("产测状态", "uint8", v, text, 0, 1, raw=data[:1])]
    if fmt == "product_set":
        # 产测指令
        if not data:
            raise ProtocolError("Data 为空")
        v = data[0]
        prod_map = cfg.get("enums", {}).get("product_test_cmd", {})
        text = prod_map.get(str(v), f"未知({v})")
        return [FieldResult("产测指令", "uint8", v, text, 0, 1, raw=data[:1])]
    if fmt == "ota_start":
        # 验签类型
        if not data:
            raise ProtocolError("Data 为空")
        v = data[0]
        sign_map = cfg.get("enums", {}).get("ota_sign_type", {})
        text = sign_map.get(str(v), f"未知({v})")
        return [FieldResult("验签类型", "uint8", v, text, 0, 1, raw=data[:1])]
    if fmt == "ota_verify":
        # 验签值（变长）
        return [FieldResult(
            "验签值", "raw", to_hex(data), to_hex(data),
            0, len(data), raw=data,
        )]
    if fmt == "mcu_status":
        # MCU 工作状态
        if not data:
            raise ProtocolError("Data 为空")
        v = data[0]
        status_map = cfg.get("enums", {}).get("mcu_status", {})
        text = status_map.get(str(v), f"未知({v})")
        return [FieldResult("MCU工作状态", "uint8", v, text, 0, 1, raw=data[:1])]
    if fmt == "raw":
        return [FieldResult(
            "Data", "raw", to_hex(data), to_hex(data),
            0, len(data), raw=data,
        )]

    raise ProtocolError(f"不支持的 format: {fmt}")


# ---------- 属性块解析 ----------

def _parse_attr_list(data: bytes, cfg: dict, force_report: bool = True) -> list[FieldResult]:
    """解析属性列表：循环 (typeid + attrid + [len] + value)。

    典型格式: 0x02 0x01 0x19 0x02 0x02 0x32 ...
    """
    results: list[FieldResult] = []
    pos = 0
    while pos < len(data):
        if pos + 2 > len(data):
            results.append(FieldResult(
                name="残留字节", type="raw", value=to_hex(data[pos:]),
                text=f"剩余 {len(data) - pos} 字节无法解析",
                offset=pos, length=len(data) - pos, raw=data[pos:],
            ))
            break

        type_byte = data[pos]
        attrid = data[pos + 1]

        # 强制上报标志
        force = bool(type_byte & TYPEID_FORCE_REPORT_BIT) if force_report else False
        typeid = type_byte & ~TYPEID_FORCE_REPORT_BIT

        type_info = TYPEID_MAP.get(typeid)
        attr_meta = _lookup_attr(cfg, attrid)

        # 计算 value 长度
        if typeid in (11, 12, 13, 14, 23, 24):
            # 变长类型：需要 len 字段
            if pos + 3 > len(data):
                results.append(FieldResult(
                    name=attr_meta.get("name", f"attrid_{attrid:02X}"),
                    type="error", value=None,
                    text=f"属性 0x{attrid:02X} 长度字段越界",
                    offset=pos, length=2, raw=data[pos:],
                ))
                break
            value_len = data[pos + 2]
            value_start = pos + 3
        else:
            # 定长类型
            value_len = type_info["size"] if type_info else 1
            value_start = pos + 2

        value_end = value_start + value_len
        if value_end > len(data):
            results.append(FieldResult(
                name=attr_meta.get("name", f"attrid_{attrid:02X}"),
                type="error", value=None,
                text=f"属性 0x{attrid:02X} 值越界 (需要 {value_len} 字节)",
                offset=pos, length=len(data) - pos, raw=data[pos:],
            ))
            break

        value_chunk = data[value_start:value_end]
        value, text = _decode_attr_value(value_chunk, typeid, attr_meta, type_info)

        # 应用属性表的取值映射
        enum_map = attr_meta.get("enum")
        if enum_map:
            text = enum_map.get(str(value), text)
        # 应用单位
        unit = attr_meta.get("unit")
        if unit and isinstance(value, (int, float)):
            text = f"{text} {unit}"
        # 应用取值范围说明
        range_text = attr_meta.get("range")
        if range_text and not enum_map:
            text = f"{text} ({range_text})"

        # 强制上报标志
        if force:
            text = f"[强制上报] {text}"

        attr_name = attr_meta.get("name", f"attrid_0x{attrid:02X}")
        results.append(FieldResult(
            name=attr_name,
            type=type_info["name"] if type_info else f"typeid_{typeid}",
            value=value,
            text=text,
            offset=pos,
            length=value_end - pos,
            raw=data[pos:value_end],
            children=[{
                "typeid": typeid,
                "type_name": type_info["name"] if type_info else "?",
                "attrid": f"0x{attrid:02X}",
                "force_report": force,
            }],
        ))

        pos = value_end

    return results


def _parse_attr_unit(data: bytes, cfg: dict) -> list[FieldResult]:
    """解析属性 id 单元（每字节一个 attrid）。"""
    results: list[FieldResult] = []
    for i, b in enumerate(data):
        attr_meta = _lookup_attr(cfg, b)
        name = attr_meta.get("name", f"attrid_0x{b:02X}")
        results.append(FieldResult(
            name=f"属性{i+1}",
            type="attrid",
            value=b,
            text=f"0x{b:02X} ({name})",
            offset=i,
            length=1,
            raw=bytes([b]),
        ))
    return results


def _decode_attr_value(chunk: bytes, typeid: int, attr_meta: dict, type_info: dict | None) -> tuple[Any, str]:
    """根据 typeid 解码属性值。"""
    if type_info is None:
        return to_hex(chunk), to_hex(chunk)

    # 应用属性表声明的类型覆盖（如果 attr_meta 中明确指定）
    declared_type = attr_meta.get("declared_type")
    if declared_type and declared_type in _DECLARED_TYPE_DECODERS:
        return _DECLARED_TYPE_DECODERS[declared_type](chunk)

    ctype = type_info.get("ctype")

    if ctype == "uint8":
        v = chunk[0]
        return v, str(v)
    if ctype == "int8":
        v = chunk[0]
        v = v if v < 128 else v - 256
        return v, str(v)
    if ctype in ("uint16_be", "int16_be", "uint32_be", "int32_be", "uint64_be", "int64_be"):
        v = int.from_bytes(chunk, "big", signed=ctype.startswith("int"))
        # 应用缩放
        scale = type_info.get("scale")
        if scale:
            return v, _format_value(v * scale)
        return v, str(v)
    if ctype in ("float32_be", "float64_be"):
        fmt = type_info["fmt"]
        v = struct.unpack(fmt, chunk)[0]
        scale = type_info.get("scale")
        if scale:
            return v, _format_value(v * scale)
        return v, _format_value(v)
    if ctype == "string":
        try:
            s = chunk.decode("ascii", errors="replace")
        except Exception:
            s = to_hex(chunk)
        return s, repr(s) if s else to_hex(chunk)
    if ctype == "string_array":
        # 纯数字字符串每两个字符转 16 进制
        try:
            s = chunk.decode("ascii", errors="replace")
        except Exception:
            s = ""
        return s, repr(s) if s else to_hex(chunk)
    if ctype == "array":
        # 数组：以 0x00 分隔的字符串
        parts = chunk.split(b"\x00")
        items = [p.decode("ascii", errors="replace") for p in parts if p]
        return items, " | ".join(items) if items else to_hex(chunk)
    if ctype == "group":
        return to_hex(chunk), f"GROUP 数据 ({len(chunk)} 字节)"
    if ctype == "date":
        return to_hex(chunk), to_hex(chunk)
    if ctype == "struct":
        return to_hex(chunk), to_hex(chunk)

    return to_hex(chunk), to_hex(chunk)


# 属性表 declared_type 自定义解码器
_DECLARED_TYPE_DECODERS = {
    "bool": lambda c: (c[0], "真" if c[0] else "假"),
    "height_mm": lambda c: (int.from_bytes(c, "big"), f"{int.from_bytes(c, 'big')} mm"),
}


def _decode_chunk(chunk: bytes, ftype: str, fdef: dict, cfg: dict) -> tuple[Any, str]:
    """解码定长字段（保留旧字段类型支持）。"""
    if ftype == "hex":
        h = chunk.hex().upper()
        return h, f"0x{h}"
    if ftype == "ascii":
        s = chunk.decode("ascii", errors="replace")
        return s, repr(s)
    if ftype == "raw":
        return chunk, to_hex(chunk)
    if ftype == "enum":
        raw = chunk[0]
        mapping = fdef.get("enum", {})
        text = mapping.get(str(raw), f"未知({raw})")
        return raw, text

    int_types = {
        "uint8": (">B", 1), "int8": (">b", 1),
        "uint16_le": ("<H", 2), "uint16_be": (">H", 2),
        "int16_le": ("<h", 2), "int16_be": (">h", 2),
        "uint32_le": ("<I", 4), "uint32_be": (">I", 4),
        "int32_le": ("<i", 4), "int32_be": (">i", 4),
        "uint64_le": ("<Q", 8), "uint64_be": (">Q", 8),
        "int64_le": ("<q", 8), "int64_be": (">q", 8),
    }
    if ftype in int_types:
        fmt, _ = int_types[ftype]
        v = struct.unpack(fmt, chunk)[0]
        return v, str(v)
    if ftype == "float32_be":
        return struct.unpack(">f", chunk)[0], ""
    if ftype == "version3":
        return (chunk[0], chunk[1], chunk[2]), f"{chunk[0]}.{chunk[1]}.{chunk[2]}"

    raise ProtocolError(f"不支持的字段类型: {ftype}")


# ---------- 属性表查询 ----------

def _lookup_attr(cfg: dict, attrid: int) -> dict:
    """根据 attrid 查询产品属性表。"""
    attr_table = cfg.get("attributes", {})
    key = f"0x{attrid:02X}"
    return attr_table.get(key, {})


# ---------- 工具 ----------

def _format_value(value: Any) -> str:
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _parse_int(v: Any) -> int:
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s.startswith("0x"):
            return int(s, 16)
        return int(s, 0)
    raise ProtocolError(f"无法解析为整数: {v}")


# ---------- 顶层解析 ----------

@dataclass
class ParseResult:
    product: str
    raw_hex: str
    cmd_code: str
    cmd_name: str
    direction: str
    description: str
    fields: list[dict] = field(default_factory=list)
    checksum_ok: bool | None = None
    length_match: bool | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        d = {
            "product": self.product,
            "raw_hex": self.raw_hex,
            "cmd_code": self.cmd_code,
            "cmd_name": self.cmd_name,
            "direction": self.direction,
            "description": self.description,
            "fields": self.fields,
            "checksum_ok": self.checksum_ok,
            "length_match": self.length_match,
        }
        if self.error:
            d["error"] = self.error
        return d


def _build_frame_fields(frame: Frame, cfg: dict, cmd_name: str = "") -> list[FieldResult]:
    """构建帧结构基础字段列表（帧头、版本、命令字、长度、校验）。"""
    frame_cfg = cfg.get("frame", {})
    results: list[FieldResult] = []
    header_size = frame_cfg.get("header_size", 2)
    ver_offset = frame_cfg.get("ver_offset", 2)
    ver_size = frame_cfg.get("ver_size", 1)
    cmd_offset = frame_cfg.get("cmd_offset", 3)
    length_offset = frame_cfg.get("length_offset", 4)
    length_size = frame_cfg.get("length_size", 2)

    # 帧头
    results.append(FieldResult(
        name="帧头",
        type="header",
        value=frame.header,
        text=f"0x{frame.header:0{header_size*2}X}",
        offset=0,
        length=header_size,
        raw=frame.raw[:header_size],
    ))

    # 版本
    results.append(FieldResult(
        name="版本",
        type="version",
        value=frame.ver,
        text=f"0x{frame.ver:02X}",
        offset=ver_offset,
        length=ver_size,
        raw=frame.raw[ver_offset:ver_offset + ver_size],
    ))

    # 命令字
    cmd_label = f"0x{frame.cmd_code:02X}"
    if cmd_name:
        cmd_label += f" {cmd_name}"
    results.append(FieldResult(
        name="命令字",
        type="cmd",
        value=frame.cmd_code,
        text=cmd_label,
        offset=cmd_offset,
        length=1,
        raw=frame.raw[cmd_offset:cmd_offset + 1],
    ))

    # 数据长度
    results.append(FieldResult(
        name="数据长度",
        type="length",
        value=frame.length,
        text=f"{frame.length} 字节 (0x{frame.length:04X})",
        offset=length_offset,
        length=length_size,
        raw=frame.raw[length_offset:length_offset + length_size],
    ))

    # 校验和
    if frame.checksum_ok is not None and frame.checksum_expected is not None:
        cs_text = to_hex(frame.checksum_expected)
        if frame.checksum_ok:
            cs_text += " [通过]"
        else:
            cs_text += " [失败]"
            if frame.checksum_actual is not None:
                cs_text += f" (期望 {to_hex(frame.checksum_actual)})"
        cs_offset = len(frame.raw) - len(frame.checksum_expected)
        results.append(FieldResult(
            name="校验和",
            type="checksum",
            value=frame.checksum_expected.hex().upper(),
            text=cs_text,
            offset=cs_offset,
            length=len(frame.checksum_expected),
            raw=frame.checksum_expected,
        ))

    return results


def parse_frame(data: bytes, cfg: dict, direction: str | None = None) -> ParseResult:
    """解析一条完整指令。

    Args:
        data: 完整帧字节
        cfg: 协议配置
        direction: 显式指定方向 ('request'/'response')；为 None 时自动识别
    """
    product = cfg.get("product", "unknown")
    raw_hex = to_hex(data)

    try:
        frame = split_frame(data, cfg)
    except ProtocolError as e:
        return ParseResult(
            product=product,
            raw_hex=raw_hex,
            cmd_code="",
            cmd_name="解析失败",
            direction="",
            description="",
            error=str(e),
        )

    cmd = find_command(cfg, frame.cmd_code)
    if cmd is None:
        frame_fields = _build_frame_fields(frame, cfg, "未知命令")
        return ParseResult(
            product=product,
            raw_hex=raw_hex,
            cmd_code=f"0x{frame.cmd_code:02X}",
            cmd_name="未知命令",
            direction="",
            description=f"协议中未定义命令字 0x{frame.cmd_code:02X}",
            checksum_ok=frame.checksum_ok,
            fields=[f.to_dict() for f in frame_fields],
        )

    # V3.0 双向命令：cmd 含 request/response 子对象
    if "request" in cmd or "response" in cmd:
        req_def = cmd.get("request", {})
        resp_def = cmd.get("response", {})

        if direction == "request":
            chosen_def, chosen_dir = req_def, "request"
        elif direction == "response":
            chosen_def, chosen_dir = resp_def, "response"
        else:
            # 自动识别：尝试两个方向，挑选无错误的；都无错时优先 response（因为请求多为 raw）
            req_results, req_ok = _try_parse_direction(frame.data, req_def, cfg)
            resp_results, resp_ok = _try_parse_direction(frame.data, resp_def, cfg)

            if resp_ok and not req_ok:
                chosen_def, chosen_dir = resp_def, "response"
                field_results = resp_results
            elif req_ok and not resp_ok:
                chosen_def, chosen_dir = req_def, "request"
                field_results = req_results
            elif req_ok and resp_ok:
                # 都成功：选字段更多的（信息量更大）
                if len(resp_results) >= len(req_results):
                    chosen_def, chosen_dir = resp_def, "response"
                    field_results = resp_results
                else:
                    chosen_def, chosen_dir = req_def, "request"
                    field_results = req_results
            else:
                # 都失败：用 response 的结果（通常更结构化）
                chosen_def, chosen_dir = resp_def, "response"
                field_results = resp_results

        # 重新解析（已选定方向）
        if direction is not None or not field_results:
            field_results = parse_data_fields(frame.data, chosen_def, cfg)

        direction_label = chosen_def.get("name", chosen_dir)

        # 组合：帧结构字段 + 数据字段
        frame_fields = _build_frame_fields(frame, cfg, cmd.get("name", ""))
        all_fields = [f.to_dict() for f in frame_fields]
        all_fields.append({
            "name": "—— 数据段 ——",
            "type": "separator",
            "value": None,
            "text": "",
        })
        all_fields.extend([f.to_dict() for f in field_results])

        return ParseResult(
            product=product,
            raw_hex=raw_hex,
            cmd_code=f"0x{frame.cmd_code:02X}",
            cmd_name=cmd.get("name", ""),
            direction=direction_label,
            description=cmd.get("description", ""),
            fields=all_fields,
            checksum_ok=frame.checksum_ok,
            length_match=(len(frame.data) == frame.length),
        )

    # 旧版定长命令
    fields_def = cmd.get("data", {})
    field_results = parse_data_fields(frame.data, fields_def, cfg)

    # 组合：帧结构字段 + 数据字段
    frame_fields = _build_frame_fields(frame, cfg, cmd.get("name", ""))
    all_fields = [f.to_dict() for f in frame_fields]
    all_fields.append({
        "name": "—— 数据段 ——",
        "type": "separator",
        "value": None,
        "text": "",
    })
    all_fields.extend([f.to_dict() for f in field_results])

    return ParseResult(
        product=product,
        raw_hex=raw_hex,
        cmd_code=f"0x{frame.cmd_code:02X}",
        cmd_name=cmd.get("name", ""),
        direction=cmd.get("direction", ""),
        description=cmd.get("description", ""),
        fields=all_fields,
        checksum_ok=frame.checksum_ok,
        length_match=(len(frame.data) == frame.length),
    )
