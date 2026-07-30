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
import re
import struct
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


class ProtocolError(Exception):
    """协议解析相关错误基类。

    - message: 开发可读的原始信息（会被日志记录）
    - friendly_msg: 面向终端用户的一句话提示（GUI弹窗/CLI stderr 用）
    调用方优先显示 friendly_msg，再把 message 和堆栈写 error.log。
    """

    default_friendly = "协议解析出现未知错误，请检查输入数据或协议配置。"

    def __init__(self, message: str, friendly_msg: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.friendly_msg = friendly_msg or self.default_friendly


class ProtocolConfigError(ProtocolError):
    """协议配置文件本身有问题（缺字段、格式错、路径不存在等）。"""

    default_friendly = "协议配置无效，请检查导入的协议文件内容。"


class HexParseError(ProtocolError):
    """HEX 文本输入解析失败。"""

    default_friendly = "HEX 输入格式不正确，请检查是否包含空格、0x 前缀和成对的 0-9/A-F 字符。"


class FrameTooShortError(ProtocolError):
    """帧总长度不足以读取帧头/版本/命令字/长度字段。"""

    default_friendly = "收到的数据太短，可能串口还没收完整一帧，或波特率/帧配置不匹配。"


class FrameHeaderMismatchError(ProtocolError):
    """帧头不匹配（通常是切帧切错位置，或协议 header 配置错）。"""

    default_friendly = "帧头不匹配，请检查当前协议帧头与对端设备是否一致。"


class FrameVersionMismatchError(ProtocolError):
    default_friendly = "帧版本不匹配，请检查协议版本是否一致。"


class FrameLengthMismatchError(ProtocolError):
    """length 字段与实际 payload 长度不一致（丢包/错包常见）。"""

    default_friendly = "帧长度字段与实际数据不符，常见原因是串口丢包、波特率错误或对端发错包。"


class FrameLengthOverflowError(ProtocolError):
    """length 字段为负或超过安全上限。"""

    default_friendly = "帧长度异常（过大或为负），可能是帧头误识别或收到错误数据。"


class FrameChecksumError(ProtocolError):
    """校验和错误。"""

    default_friendly = "帧校验和错误，请检查通信是否受干扰、波特率/停止位是否匹配。"


class AttrLengthMismatchError(ProtocolError):
    """属性列表里单个属性的 length 超过剩余字节。"""

    default_friendly = "属性长度越界，常见于协议配置错误或帧丢包。"


class AttrTypeUnsupportedError(ProtocolError):
    default_friendly = "遇到未定义的属性类型，请升级解析程序或核对协议版本。"


class AttrValueParseError(ProtocolError):
    """属性值按 type 解析失败（字节不够、格式错等）。"""

    default_friendly = "属性值解析失败，常见于属性类型与实际数据不一致。"


class DataFieldParseError(ProtocolError):
    """命令 data_def 定长解析失败。"""

    default_friendly = "命令字段解析失败，请检查命令的字段定义是否与实际帧数据匹配。"


class ChecksumAlgoError(ProtocolError):
    default_friendly = "协议帧校验算法未配置，请检查帧配置 checksum 部分。"


class CoversConfigError(ProtocolError):
    default_friendly = "协议校验覆盖范围配置不合法。"


class FormatUnsupportedError(ProtocolError):
    default_friendly = "数据格式不支持。"


class EnumUnmatchedError(ProtocolError):
    default_friendly = "数值未匹配到任何枚举项，显示原始值。"  # 通常不抛，仅兜底


class IntegerParseError(ProtocolError):
    default_friendly = "配置项无法解析为整数，请检查协议配置。"


class DocxImportError(ProtocolError):
    """Word 协议导入失败。"""

    default_friendly = "Word 协议解析失败，请确认文档格式与导入模板一致。"


class UpdaterError(ProtocolError):
    """在线更新过程中可预期的错误。"""
    default_friendly = "在线更新失败，请检查网络或稍后重试。"


class EncodeFrameError(ProtocolError):
    """组包（发送编码）过程中可预期的错误：格式错/枚举错/字段值不合法等。"""
    default_friendly = "指令组包失败，请检查命令字段取值或协议配置。"


def classify_protocol_error(exc: Exception) -> tuple[str, str]:
    """把任意异常收敛为 (friendly_msg, debug_message) 二元组。

    - ProtocolError 子类自带 friendly_msg；
    - 其他 Exception 统一给"未知错误 + 写入 error.log"的友好提示，避免把堆栈甩给用户。
    """
    if isinstance(exc, ProtocolError):
        debug = getattr(exc, "message", None) or str(exc)
        return exc.friendly_msg, debug
    friendly = "程序遇到未知错误，详情已记录到 error.log，请反馈给开发者。"
    try:
        debug = f"{type(exc).__name__}: {exc}"
    except Exception:
        debug = type(exc).__name__
    return friendly, debug


def _log_error_to_disk(exc: Exception) -> Path:
    """把异常堆栈写入工作目录 error.log，失败时静默回退到临时目录。

    - CLI/GUI/Monitor 共用，保证错误日志落盘一致；
    - 写入失败绝对不抛，不因为写日志导致二次崩溃。
    """
    import sys
    import traceback

    try:
        log_path = Path.cwd() / "error.log"
    except Exception:
        try:
            log_path = Path(__file__).resolve().parent / "error.log"
        except Exception:
            return Path("error.log")

    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {type(exc).__name__}: {exc}\n")
            f.write(stack)
            f.write("-" * 60 + "\n")
    except Exception:
        try:
            import tempfile
            tmp_dir = Path(tempfile.gettempdir())
            log_path = tmp_dir / "protocol_parser_error.log"
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            stack = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"[{ts}] {type(exc).__name__}: {exc}\n{stack}{'-'*60}\n")
        except Exception:
            pass
    return log_path


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
# 变长 typeid 集合（需要 length 字段），编解码共用
VARLEN_TYPEIDS = frozenset({11, 12, 13, 14, 23, 24})

# ---------- 配置加载 ----------

def load_protocol(path: str | Path) -> dict:
    p = Path(path)
    if not p.exists():
        raise ProtocolConfigError(f"协议配置文件不存在: {p}")
    try:
        with p.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError) as e:
        raise ProtocolConfigError(f"协议配置文件读取失败: {e}") from e
    _validate_protocol(cfg)
    return cfg


def _validate_protocol(cfg: dict) -> None:
    if "product" not in cfg:
        raise ProtocolConfigError("协议配置缺少 'product' 字段")
    if "commands" not in cfg or not isinstance(cfg["commands"], list):
        raise ProtocolConfigError("协议配置缺少 'commands' 列表")


# ---------- 内置 V3.0 协议（CLI/GUI 共用，避免 CLI 依赖 gui.py） ----------
_builtin_v3: dict | None = None


def _default_protocol_dir() -> Path:
    """返回默认协议目录（兼容 PyInstaller / 开发模式）。"""
    try:
        import sys as _sys
        if getattr(_sys, "frozen", False):
            exe_dir = Path(_sys.executable).resolve().parent
            proto_dir = exe_dir / "product"
            proto_dir.mkdir(parents=True, exist_ok=True)
            return proto_dir
    except Exception:
        pass
    dev = Path(__file__).resolve().parent.parent / "product"
    try:
        dev.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return dev


def _load_builtin_v3_fallback() -> dict:
    """加载内置 v3 协议（优先用户 product/v3_serial.json，否则回退空结构）。"""
    base = _default_protocol_dir()
    candidate = base / "v3_serial.json"
    if candidate.exists():
        try:
            return load_protocol(candidate)
        except ProtocolError:
            pass
    # 也尝试包内的 product 资源
    try:
        bundled = Path(__file__).resolve().parent.parent / "product" / "v3_serial.json"
        if bundled.exists() and bundled.resolve() != candidate.resolve():
            return load_protocol(bundled)
    except Exception:
        pass
    return {
        "product": "串口3.0协议",
        "description": "内置基础协议",
        "commands": [],
        "frame": {},
        "enums": {},
        "attributes": {},
    }


def get_builtin_v3(refresh: bool = False) -> dict:
    """获取内置串口 3.0 基础协议（模块级缓存）。

    CLI / GUI 共用，这样 CLI 不用依赖 gui.py（避免在没装 tkinter 的环境运行失败）。
    """
    global _builtin_v3
    if refresh or _builtin_v3 is None:
        _builtin_v3 = _load_builtin_v3_fallback()
    return _builtin_v3


def merge_protocol(base: dict, override: dict) -> dict:
    """将 override 协议合并到 base 协议上，override 优先级更高。

    合并规则：
    - product / description / version：使用 override 的
    - frame：使用 override 的（如果存在且非空），否则保留 base
    - commands：以 cmd_code 为键合并，保留 base 的 request/response 格式定义，
      只用 override 的 name/description 覆盖
    - attributes：以 attrid 为键合并，override 覆盖/补充 base
    - enums：递归合并，override 覆盖 base
    """
    import copy
    result = copy.deepcopy(base)

    # 基本元信息
    for key in ("product", "description", "version", "_imported_from"):
        if key in override:
            result[key] = override[key]

    # frame
    if "frame" in override and override["frame"]:
        result["frame"] = copy.deepcopy(override["frame"])

    # commands 合并（以 cmd_code 为键）
    # 策略：完全保留 base 的命令定义（包括 name/description/format），
    #       只有 override 中独有的命令才添加
    if "commands" in override:
        import copy as _copy
        def _cmd_key(c: dict):
            raw = c.get("cmd_code", c.get("code", c.get("id", c.get("cmd"))))
            try:
                if isinstance(raw, str):
                    return int(raw, 0) & 0xFF
                return int(raw) & 0xFF
            except Exception:
                return str(raw)

        base_cmds = {}
        for c in result.get("commands", []):
            base_cmds[_cmd_key(c)] = _copy.deepcopy(c)

        for cmd in override["commands"]:
            if not isinstance(cmd, dict):
                continue
            key = _cmd_key(cmd)
            if key in base_cmds:
                # 同 cmd：产品侧覆盖 name/description 等，保留 base 的 request/response 格式
                merged = base_cmds[key]
                for k, v in cmd.items():
                    if k in ("request", "response") and isinstance(v, dict):
                        base_block = merged.get(k) if isinstance(merged.get(k), dict) else {}
                        merged[k] = {**base_block, **v}
                    else:
                        merged[k] = v
                base_cmds[key] = merged
            else:
                base_cmds[key] = _copy.deepcopy(cmd)

        result["commands"] = list(base_cmds.values())

    # attributes 合并（以 attrid 为键）
    if "attributes" in override:
        base_attrs = result.get("attributes", {})
        base_attrs.update(override["attributes"])
        result["attributes"] = base_attrs

    # enums 合并
    if "enums" in override:
        base_enums = result.get("enums", {})
        for enum_name, enum_map in override["enums"].items():
            if enum_name in base_enums:
                merged_enum = copy.deepcopy(base_enums[enum_name])
                merged_enum.update(enum_map)
                base_enums[enum_name] = merged_enum
            else:
                base_enums[enum_name] = enum_map
        result["enums"] = base_enums

    return result


# ---------- 字节工具 ----------

# 匹配 0x/0X 开头的“合法 hex 前缀”：
#   - 0x 前面不能是字母数字（避免把 AA0x11 的 0x11 单独剥掉，造成 AA0x11 = AA11 的假结果）
#   - 0x 后面必须紧跟 hex 字符
#   - 合法用法：分隔符后 0x11 / 逗号 0xAA / (0xAA, 0xBB) / [0xAA]  都会被识别
_RE_PREFIXED_HEX = re.compile(r"(?i)(?<![0-9a-zA-Z])0x(?=[0-9a-f])([0-9a-f]+)")
# 把“残留的脏 0x”整段删掉：
#   匹配 0x（后面不是 hex，或者后面是 hex 但前面跟了字母数字=不是词首，比如 AA0x11）。
#   这样 "XX0xYZ" → 变成 XXYZ → 因为 YZ 是字母但非 hex，再 findall hex 不会匹配 → 抛“没有任何 hex”；
#   这样 "AA0x11" → 剥掉 0x11 中的 0x 后还要再保证前缀不剥，实际上本正则先把 AA0x11 里的 0x11 当作“夹在字母里的 0x”，
#   所以我们在 sub 时把 _RE_PREFIXED_HEX 合法的先剥，剩下脏 0x（AA0x11这种）里的 0x 删掉 → AA11？
#   这行为其实比之前的假通过更合理。
_RE_STRIP_BAD_0X = re.compile(r"(?i)0x(?!(?<![0-9a-zA-Z])0x[0-9a-f])")

# 纯 hex 字符段
_RE_PURE_HEX = re.compile(r"(?i)[0-9a-f]+")


def parse_hex_input(text: str) -> bytes:
    """把用户输入的 hex 字符串解析为 bytes。

    严格版本（修复了旧实现的全局 replace('0x','') 静默错删）：
      1) 仅“词首合法”的 0xHEX（0x 前不是字母数字，0x 后紧跟 hex）才剥掉前缀；
         - 合法： 0xAA,0xBB ; (0xAA 0xBB) ; 0x01 0x02
         - 不合法（夹在字符串中间）： AA0x11（A 是字母数字，所以整体视作文本，0x 不应剥）
      2) 对“残留脏 0x”（后跟非 hex，或夹在字母数字里）直接删掉 0x 两个字符，
         再对剩下文本 findall 纯 hex 段合并，严格判断奇偶。
    """
    if text is None:
        raise HexParseError("输入为空")
    raw = str(text)

    # Step 1: 合法 0xHEX （词首，前面不是字母数字）→ 剥 0x 前缀，保留后面 hex
    s1 = _RE_PREFIXED_HEX.sub(lambda m: m.group(1), raw)
    # Step 2: 剔除残留 0x（两种情形都删 0x 两个字符）：
    #   a) 前面是字母数字（如 AA0x11 里的 0x，整体不合法，不要拼成 AA11 假通过）
    #   b) 后跟非 hex（如 0xZZ11 里的 0x，后跟 Z 不合法，避免残留 0 造成假偶数）
    s2 = re.sub(r"(?i)(?<=[0-9a-zA-Z])0x|0x(?![0-9a-f])", "", s1)

    # Step 3: 纯 hex 段合并
    joined = "".join(_RE_PURE_HEX.findall(s2))
    if not joined:
        raise HexParseError("输入中没有任何可解析的 hex 字符")
    if len(joined) % 2 != 0:
        raise HexParseError(
            f"hex 字符总数为奇数({len(joined)})，无法配对: {joined}"
        )
    try:
        return bytes.fromhex(joined)
    except ValueError as e:
        raise HexParseError(f"hex 解析失败: {e}") from e


def _read_length_field(buf: bytes, offset: int, width: int = 1, byte_order: str = "big") -> int:
    if not isinstance(width, int) or width <= 0:
        raise AttrLengthMismatchError(f"长度域宽度非法: {width!r}")
    end = offset + width
    if end > len(buf):
        raise AttrLengthMismatchError(
            f"长度域越界: offset={offset}, width={width}B, 剩余 {max(0, len(buf) - offset)}B"
        )
    return int.from_bytes(buf[offset:end], byteorder=byte_order)


def _check_remaining(buf: bytes, offset: int, need: int, *, label: str) -> None:
    if need < 0:
        raise AttrLengthMismatchError(f"{label}: 需要字节数为负 {need}")
    left = len(buf) - offset
    if left < need:
        raise AttrLengthMismatchError(
            f"{label}: 剩余字节不足 (offset={offset}, 需要 {need}B, 实际剩余 {left}B, 总长 {len(buf)}B)"
        )


def to_hex(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


# ---------- 校验算法 ----------

def calc_checksum(data: bytes, algorithm: str) -> bytes:
    """计算校验字节。algorithm 不区分大小写，支持别名。"""
    algo = (algorithm or "").strip().lower().replace("-", "").replace("_", "")

    # ---- 8 位 ----
    # ADD8 / sum：字节累加 & 0xFF
    if algo in ("sum", "add8", "add"):
        return bytes([sum(data) & 0xFF])

    # 0-ADD8：对 ADD8 取补（(0 - sum) & 0xFF），常见于部分协议
    if algo in ("0add8", "0-add8", "negadd8", "add8neg"):
        return bytes([(-sum(data)) & 0xFF])

    # XOR8
    if algo in ("xor", "xor8"):
        v = 0
        for b in data:
            v ^= b
        return bytes([v & 0xFF])

    # CRC8（多项式 0x07，初值 0xFF）
    if algo == "crc8":
        crc = 0xFF
        for b in data:
            crc ^= b
            for _ in range(8):
                crc = ((crc << 1) ^ 0x07) & 0xFF if (crc & 0x80) else ((crc << 1) & 0xFF)
        return bytes([crc])

    # ---- 16 位 ----
    # ADD16：累加 & 0xFFFF，大端 2 字节
    if algo in ("add16", "sum16"):
        s = sum(data) & 0xFFFF
        return bytes([(s >> 8) & 0xFF, s & 0xFF])

    # Modbus CRC16（poly 0xA001，初值 0xFFFF，低字节在前）
    if algo in ("modbuscrc16", "modbus", "crc16modbus"):
        crc = 0xFFFF
        for b in data:
            crc ^= b
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return bytes([crc & 0xFF, (crc >> 8) & 0xFF])  # little-endian

    # CRC16-CCITT（poly 0x1021，初值 0xFFFF，高字节在前）
    if algo in ("ccittcrc16", "crc16ccitt", "ccitt"):
        crc = 0xFFFF
        for b in data:
            crc ^= (b << 8) & 0xFFFF
            for _ in range(8):
                if crc & 0x8000:
                    crc = ((crc << 1) ^ 0x1021) & 0xFFFF
                else:
                    crc = (crc << 1) & 0xFFFF
        return bytes([(crc >> 8) & 0xFF, crc & 0xFF])  # big-endian

    # ---- 32 位 ----
    # CRC32（IEEE，poly 0xEDB88320，初值 0xFFFFFFFF，结果取反，小端）
    if algo == "crc32":
        crc = 0xFFFFFFFF
        for b in data:
            crc ^= b
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ 0xEDB88320
                else:
                    crc >>= 1
        crc ^= 0xFFFFFFFF
        return bytes([
            crc & 0xFF,
            (crc >> 8) & 0xFF,
            (crc >> 16) & 0xFF,
            (crc >> 24) & 0xFF,
        ])

    raise ChecksumAlgoError(f"不支持的校验算法: {algorithm}")

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
        raise FrameTooShortError(f"数据过短 ({len(data)}B)，无法读取帧头")
    header = int.from_bytes(data[:header_size], "big")
    expected_header = _parse_int(frame_cfg.get("header", "0xA5A5"))
    if header != expected_header:
        raise FrameHeaderMismatchError(
            f"帧头不匹配: 期望 0x{expected_header:04X}, 实际 0x{header:04X}"
        )

    # 版本
    ver_offset = frame_cfg.get("ver_offset", 2)
    ver_size = frame_cfg.get("ver_size", 1)
    if len(data) < ver_offset + ver_size:
        raise FrameTooShortError(
            f"数据过短 ({len(data)}B)，无法读取版本字段 (offset={ver_offset}, size={ver_size})"
        )
    ver = int.from_bytes(data[ver_offset:ver_offset + ver_size], "big")
    expected_ver = frame_cfg.get("ver")
    if expected_ver is not None and ver != _parse_int(expected_ver):
        raise FrameVersionMismatchError(f"版本不匹配: 期望 {_parse_int(expected_ver)}, 实际 {ver}")

    # 命令字
    cmd_offset = frame_cfg.get("cmd_offset", 3)
    if len(data) <= cmd_offset:
        raise FrameTooShortError(
            f"数据过短 ({len(data)}B)，无法读取命令字 (offset={cmd_offset})"
        )
    cmd_code = data[cmd_offset]

    # 数据长度
    length_offset = frame_cfg.get("length_offset", 4)
    length_size = frame_cfg.get("length_size", 2)
    length_byte_order = frame_cfg.get("length_byte_order", "big")
    if len(data) < length_offset + length_size:
        raise FrameTooShortError(
            f"数据过短 ({len(data)}B)，无法读取长度字段 (offset={length_offset}, size={length_size})"
        )
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
            raise CoversConfigError(f"不支持的 covers: {covers}")

        checksum_actual = calc_checksum(covered, cs_algo)
        checksum_ok = checksum_expected == checksum_actual

    # Data 区域：严格校验 length 字段，不要静默用实际剩余字节（否则掩盖通信丢包/切包错误）
    data_start = length_offset + length_size
    max_available = data_end - data_start
    if length > max_available:
        raise FrameLengthMismatchError(
            f"帧长度字段与实际不匹配: length={length}, "
            f"数据区最大可用 {max_available}B (offset={data_start}, checksum前={data_end}, 帧总长={len(data)}B)"
        )
    if length < 0:
        raise FrameLengthOverflowError(f"帧长度字段为负: {length}")
    payload = data[data_start:data_start + length]

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
            if isinstance(self.raw, (bytes, bytearray, memoryview)):
                d["raw"] = bytes(self.raw).hex().upper()
            else:
                d["raw"] = str(self.raw)
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
        raise DataFieldParseError(
            f"字段 '{fdef.get('name', '?')}' 越界: offset={offset}, length={length}, 帧总长 {len(data)}"
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
        return _parse_attr_list(data, cfg, force_report=data_def.get("force_report", True))
    if fmt == "attr_unit":
        return _parse_attr_unit(data, cfg)
    if fmt == "msg_id_then_attr_unit":
        if not data:
            raise AttrValueParseError("Data 为空，无法读取消息 id")
        msg_id = data[0]
        results = [FieldResult(
            name="消息id", type="uint8", value=msg_id, text=str(msg_id),
            offset=0, length=1, raw=data[:1],
        )]
        results.extend(_parse_attr_unit(data[1:], cfg))
        return results
    if fmt == "msg_id_then_attr":
        if not data:
            raise AttrValueParseError("Data 为空，无法读取消息 id")
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
        if not data:
            raise AttrValueParseError("Data 为空，无法读取消息 id")
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
        if len(data) < 2:
            raise AttrValueParseError("Data 过短，无法读取消息 id + 行为 id")
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
        if not data:
            raise AttrValueParseError("Data 为空，无法读取事件 id")
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
            raise AttrValueParseError("Data 为空，无法读取错误码")
        err = data[0]
        err_map = cfg.get("enums", {}).get("errcode", {})
        text = err_map.get(str(err), f"未知({err})")
        return [FieldResult(
            name="错误码", type="uint8", value=err, text=text,
            offset=0, length=1, raw=data[:1],
        )]
    if fmt == "errcode_then_attr":
        if not data:
            raise AttrValueParseError("Data 为空，无法读取错误码")
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
        if len(data) < 4:
            raise AttrValueParseError("Data 长度不足 4 字节 (错误码 + 分区 + 升级包)")
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
        if len(data) < 3:
            raise AttrValueParseError("Data 长度不足 3 字节 (分区 + 升级包序号)")
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
        if len(data) < 6:
            raise AttrValueParseError("Data 长度不足 6 字节 (分区 + CRC32)")
        partition = int.from_bytes(data[:2], "big")
        crc = int.from_bytes(data[2:6], "big")
        return [
            FieldResult("分区序号", "uint16_be", partition, str(partition), 0, 2, raw=data[:2]),
            FieldResult("CRC32", "uint32_be", crc, f"0x{crc:08X}", 2, 4, raw=data[2:6]),
        ]
    if fmt == "partition_then_attr":
        if len(data) < 6:
            raise AttrValueParseError("Data 长度不足 6 字节 (分区 + CRC32)")
        partition = int.from_bytes(data[:2], "big")
        crc = int.from_bytes(data[2:6], "big")
        return [
            FieldResult("分区序号", "uint16_be", partition, str(partition), 0, 2, raw=data[:2]),
            FieldResult("CRC32", "uint32_be", crc, f"0x{crc:08X}", 2, 4, raw=data[2:6]),
        ]
    if fmt == "dev_version":
        if len(data) < 3:
            raise AttrValueParseError("Data 长度不足 3 字节 (主/次/修正版本号)")
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
    if fmt == "dev_info":
        attr_results = _parse_attr_list(data, cfg, force_report=False)
        for fr in attr_results:
            children = fr.children or []
            if children and children[0].get("attrid") == "0xF7":
                fr.name = "设备PID"
                raw_val = fr.value
                if isinstance(raw_val, int):
                    fr.text = f"{raw_val} (0x{raw_val:08X})"
                continue
            if children and children[0].get("attrid") == "0xF5":
                fr.name = "产品Model"
                if isinstance(fr.value, str) and fr.text.startswith("'") and fr.text.endswith("'"):
                    fr.text = fr.value
                continue
            if children and children[0].get("attrid") == "0xF3":
                fr.name = "设备属性列表"
                if isinstance(fr.raw, bytes) and len(fr.raw) >= 4:
                    inner_value = fr.raw[4:]
                    if inner_value:
                        try:
                            inner_results = _parse_attr_list(inner_value, cfg, force_report=False)
                            fr.text = f"共 {len(inner_results)} 个属性"
                            fr.children = (children or []) + [
                                {"__inner_field__": True, **ir.to_dict()}
                                for ir in inner_results
                            ]
                        except Exception:
                            pass
                continue
        return attr_results
    if fmt == "net_config":
        if not data:
            raise AttrValueParseError("Data 为空，无法读取配网方式")
        v = data[0]
        net_map = cfg.get("enums", {}).get("net_config_type", {})
        text = net_map.get(str(v), f"未知({v})")
        return [FieldResult("配网方式", "uint8", v, text, 0, 1, raw=data[:1])]
    if fmt == "module_status":
        if not data:
            raise AttrValueParseError("Data 为空，无法读取模组状态")
        v = data[0]
        status_map = cfg.get("enums", {}).get("module_status", {})
        text = status_map.get(str(v), f"未知({v})")
        return [FieldResult("模组工作状态", "uint8", v, text, 0, 1, raw=data[:1])]
    if fmt == "heartbeat_resp":
        if not data:
            raise AttrValueParseError("Data 为空，无法读取心跳响应")
        v = data[0]
        hb_map = cfg.get("enums", {}).get("heartbeat_resp", {})
        text = hb_map.get(str(v), f"未知({v})")
        return [FieldResult("MCU心跳值", "uint8", v, text, 0, 1, raw=data[:1])]
    if fmt == "get_time":
        if not data:
            raise AttrValueParseError("Data 为空，无法读取时区")
        tz = data[0]
        tz_val = tz if tz < 128 else tz - 256
        return [FieldResult("时区", "int8", tz_val, f"UTC{'+' if tz_val >= 0 else ''}{tz_val}", 0, 1, raw=data[:1])]
    if fmt == "get_time_resp":
        if len(data) < 9:
            raise AttrValueParseError("Data 长度不足 9 字节，无法解析时间响应")
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
        return _parse_attr_list(data, cfg, force_report=True)
    if fmt == "product_test":
        if not data:
            raise AttrValueParseError("Data 为空，无法读取产测状态")
        v = data[0]
        prod_map = cfg.get("enums", {}).get("product_test_status", {})
        text = prod_map.get(str(v), f"未知({v})")
        return [FieldResult("产测状态", "uint8", v, text, 0, 1, raw=data[:1])]
    if fmt == "product_set":
        if not data:
            raise AttrValueParseError("Data 为空，无法读取产测指令")
        v = data[0]
        prod_map = cfg.get("enums", {}).get("product_test_cmd", {})
        text = prod_map.get(str(v), f"未知({v})")
        return [FieldResult("产测指令", "uint8", v, text, 0, 1, raw=data[:1])]
    if fmt == "ota_start":
        if not data:
            raise AttrValueParseError("Data 为空，无法读取 OTA 验签类型")
        v = data[0]
        sign_map = cfg.get("enums", {}).get("ota_sign_type", {})
        text = sign_map.get(str(v), f"未知({v})")
        return [FieldResult("验签类型", "uint8", v, text, 0, 1, raw=data[:1])]
    if fmt == "ota_verify":
        return [FieldResult(
            "验签值", "raw", to_hex(data), to_hex(data),
            0, len(data), raw=data,
        )]
    if fmt == "mcu_status":
        if not data:
            raise AttrValueParseError("Data 为空，无法读取 MCU 工作状态")
        v = data[0]
        status_map = cfg.get("enums", {}).get("mcu_status", {})
        text = status_map.get(str(v), f"未知({v})")
        return [FieldResult("MCU工作状态", "uint8", v, text, 0, 1, raw=data[:1])]
    if fmt == "raw":
        return [FieldResult(
            "Data", "raw", to_hex(data), to_hex(data),
            0, len(data), raw=data,
        )]

    raise FormatUnsupportedError(f"不支持的 format: {fmt}")


# ---------- 属性块解析 ----------

def _parse_attr_list(data: bytes, cfg: dict, force_report: bool = True) -> list[FieldResult]:
    """解析属性列表：循环 (typeid + attrid + [len] + value)。

    典型格式: 0x02 0x01 0x19 0x02 0x02 0x32 ...

    安全改进:
    1. 变长类型 length 域默认 2 字节（原代码写 1 字节注释却读 2 字节是对的，但现在抽成
       _read_length_field 统一读取，预留扩展：如果 cfg['attributes']['__length_width__'] 或
       单个 attr.meta['length_width'] 指定了 1/2/4，按其 width 读长度）。
    2. 每次 value_end 越界时不再静默生成 error Field 然后 break，而是抛出
       ProtocolError("attr value 越界 ... length mismatch")，让调用方明确知道包不完整。
       如果最终要容错，调用方 try/except 后降级显示。
    3. 定长 typeid 未知时默认 size=1 不变，但会显式 _check_remaining 校验剩余。
    """
    results: list[FieldResult] = []
    pos = 0
    total = len(data)

    # 当前协议的变长 length 域宽度（默认 2 字节，与原有实现保持二进制兼容；
    # 需要 1 字节的老协议可以在 JSON 里 __length_width__ = 1 指定，按 attr 单独指定也行）
    DEFAULT_VARLEN_WIDTH = 2
    protocol_len_width = int((cfg.get("attributes") or {}).get("__length_width__", DEFAULT_VARLEN_WIDTH))
    if protocol_len_width not in (1, 2, 4):
        protocol_len_width = DEFAULT_VARLEN_WIDTH

    while pos < total:
        _check_remaining(data, pos, 2, label=f"属性头 (pos={pos})")
        type_byte = data[pos]
        attrid = data[pos + 1]

        # 强制上报标志
        force = bool(type_byte & TYPEID_FORCE_REPORT_BIT) if force_report else False
        typeid = type_byte & ~TYPEID_FORCE_REPORT_BIT

        type_info = TYPEID_MAP.get(typeid)
        attr_meta = _lookup_attr(cfg, attrid)

        # 读取 value_len
        if typeid in VARLEN_TYPEIDS:
            # 变长：优先用单个属性声明的 width，没有就用协议级的默认值
            width_candidates = [
                attr_meta.get("length_width") if isinstance(attr_meta, dict) else None,
                protocol_len_width,
            ]
            width: int = DEFAULT_VARLEN_WIDTH
            for w in width_candidates:
                if isinstance(w, int) and w in (1, 2, 4):
                    width = w
                    break
            value_len = _read_length_field(data, pos + 2, width=width, byte_order="big")
            value_start = pos + 2 + width
        else:
            # 定长：未知 typeid 默认 1B，已经过 _check_remaining(pos, 2)，定长 value_start 一定安全
            value_len = type_info["size"] if type_info else 1
            value_start = pos + 2

        value_end = value_start + value_len
        _check_remaining(
            data,
            value_start,
            value_len,
            label=(
                f"属性 0x{attrid:02X} (typeid={typeid}, "
                f"name={attr_meta.get('cn_name') or attr_meta.get('name') or '?'}) "
                f"value_len={value_len}"
            ),
        )
        value_chunk = data[value_start:value_end]
        value, raw_text = _decode_attr_value(value_chunk, typeid, attr_meta, type_info)

        # 属性显示名：优先中文 cn_name，没有用英文 name，再没有就 fallback 到 attrid
        cn_name = attr_meta.get("cn_name") or ""
        en_name = attr_meta.get("name", "")
        display_name = (cn_name if cn_name else (en_name if en_name else f"attrid_0x{attrid:02X}"))

        # 应用属性表的取值映射（枚举中文标签），并记录是否命中了枚举，方便拼 text
        enum_map = attr_meta.get("enum")
        enum_hit = False
        value_label = raw_text
        if enum_map and isinstance(value, (int, float)):
            k = str(int(value)) if isinstance(value, bool) or float(value).is_integer() else str(value)
            if k in enum_map:
                value_label = enum_map[k]
                enum_hit = True

        # 最终显示 text：
        #   A. 如果有中文属性名 → 总是放在最前面，直接拼接 value_label（用户要的「属性=值」连读）
        #      例：照明打开 / 模式吹风 / 吹风档位高档 / 设定温度30 / 摆风关闭
        #   B. 没有中文属性名 → value_label 原样
        if cn_name:
            text = f"{cn_name}{value_label}"
        else:
            text = value_label

        # 应用单位（只在非枚举或非直拼时附加；或即使直拼也保留常见度单位附加：摄氏度、% 等也可追加）
        unit = attr_meta.get("unit")
        if unit and isinstance(value, (int, float)):
            if enum_hit and cn_name:
                # 温度档：「设定温度30 ℃」→ 温度后带单位也直观；但如果是文本档（打开/关闭）就不附加
                if unit and all(c in "℃°CF%RH%rh%克g公斤kg小时h分m秒s" for c in unit) and not (isinstance(value_label, str) and any("\u4e00" <= ch <= "\u9fff" for ch in value_label)):
                    text = f"{text} {unit}"
            else:
                text = f"{text} {unit}"
        # 应用取值范围说明（没命中枚举时附加，避免多余信息）
        range_text = attr_meta.get("range")
        if range_text and not enum_hit:
            text = f"{text} ({range_text})"

        # 强制上报标志
        if force:
            text = f"[强制上报] {text}"

        results.append(FieldResult(
            name=display_name,
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
                "attr_en_name": en_name,
                "attr_cn_name": cn_name,
                "value_raw": raw_text,
                "value_label": value_label,
                "enum_hit": enum_hit,
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
        cn_name = attr_meta.get("cn_name") or ""
        en_name = attr_meta.get("name", "")
        name = cn_name if cn_name else (en_name if en_name else f"attrid_0x{b:02X}")
        label = name
        if cn_name and en_name and cn_name != en_name:
            label = f"{cn_name}（{en_name}）"
        results.append(FieldResult(
            name=f"属性{i+1}",
            type="attrid",
            value=b,
            text=f"0x{b:02X} ({label})",
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

    raise AttrTypeUnsupportedError(f"不支持的字段类型: {ftype}")


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
    raise IntegerParseError(f"无法解析为整数: {v}")


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
            # 用户选择"模组发送"，匹配 name="模组→MCU" 的格式
            if req_def.get("name") == "模组→MCU":
                chosen_def, chosen_dir = req_def, "request"
            elif resp_def.get("name") == "模组→MCU":
                chosen_def, chosen_dir = resp_def, "response"
            else:
                chosen_def, chosen_dir = req_def, "request"
        elif direction == "response":
            # 用户选择"MCU发送"，匹配 name="MCU→模组" 的格式
            if req_def.get("name") == "MCU→模组":
                chosen_def, chosen_dir = req_def, "request"
            elif resp_def.get("name") == "MCU→模组":
                chosen_def, chosen_dir = resp_def, "response"
            else:
                chosen_def, chosen_dir = resp_def, "response"
        else:
            # 自动识别：尝试两个方向，挑选无错误的；都无错时优先 request
            # （V3.0 协议中大多数命令是模组主动发起的：心跳、查询、状态上报等）
            req_results, req_ok = _try_parse_direction(frame.data, req_def, cfg)
            resp_results, resp_ok = _try_parse_direction(frame.data, resp_def, cfg)

            if req_ok and not resp_ok:
                chosen_def, chosen_dir = req_def, "request"
                field_results = req_results
            elif resp_ok and not req_ok:
                chosen_def, chosen_dir = resp_def, "response"
                field_results = resp_results
            elif req_ok and resp_ok:
                # 都成功：优先 request（模组发送），因为模组是主动方
                chosen_def, chosen_dir = req_def, "request"
                field_results = req_results
            else:
                # 都失败：用 request 的结果
                chosen_def, chosen_dir = req_def, "request"
                field_results = req_results

        # 重新解析（已选定方向）
        if direction is not None or not field_results:
            try:
                field_results = parse_data_fields(frame.data, chosen_def, cfg)
            except ProtocolError as e:
                field_results = [FieldResult(
                    name="解析错误", type="error", value=None,
                    text=f"{e}（方向={chosen_dir}，数据长度={len(frame.data)}）",
                    offset=0, length=len(frame.data), raw=to_hex(frame.data),
                )]

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
    try:
        field_results = parse_data_fields(frame.data, fields_def, cfg)
    except ProtocolError as e:
        field_results = [FieldResult(
            name="解析错误", type="error", value=None,
            text=f"{e}（数据长度={len(frame.data)}）",
            offset=0, length=len(frame.data), raw=to_hex(frame.data),
        )]

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


# =========================================================================
# 协议型组包 / 发送编码（V3.0 编码器）
# =========================================================================
#
# 思路：和 parse_frame 共用 frame cfg 与 calc_checksum，保证帧头、长度、CRC
#       与解析侧完全对称；用户侧只要给 cmd_code + 简化版"字段字典"即可。
#
# 支持的简化字段输入：
#   - raw              : bytes/bytearray/str(HEX 字符串)  → 直接做 data 段
#   - uint8            : int → 1 字节
#   - uint16_be / int16_be / uint32_be / uint32_be 等 → 整数→多字节
#   - enum             : int → 1 字节
#   - msg_id           : int → 1 字节消息号
#   - module_status / heartbeat_resp / net_config_type / mcu_status / errcode /
#     product_test_status / product_test_cmd / ota_sign_type
#                      : 对应 1 字节枚举（按 cfg.enums 映射名→值；用户给字符串也行）
#   - attr_list        : [(attrid_int_or_hex, value, typeid_int), ...] 简写列表，每单元按 typeid 编码
#   - attr_unit        : [attrid_int_or_hex, ...]  每字节一个 attrid
#
# 方向缺省：request（"模组发送"对应方向=request；用户也可写 "request"/"response" 字符串）
# =========================================================================


def _encode_int(v, width: int, byte_order: str, signed: bool) -> bytes:
    """统一的整数编码（对 Enum/字符串/十六进制字符串也兜底转 int）。"""
    try:
        if isinstance(v, bool):
            v_int = int(v)
        elif isinstance(v, int):
            v_int = v
        elif isinstance(v, str):
            s = v.strip()
            if s.lower().startswith("0x"):
                v_int = int(s, 16)
            else:
                v_int = int(s, 0)
        else:
            v_int = int(v)
    except Exception as e:
        raise EncodeFrameError(f"整数编码失败：{v!r}，原因：{e}") from e
    try:
        return int(v_int).to_bytes(width, byte_order, signed=signed)
    except Exception as e:
        raise EncodeFrameError(f"整数 {v_int!r} 无法用 {width} 字节编码（signed={signed}）：{e}") from e


def _enum_name_to_value(cfg: dict, enum_name: str, value) -> int:
    """把枚举值解析为 uint8：int/0x 字符串原样；若给的是字符串（如"OK"）就按 enums 反查。"""
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value & 0xFF
    if isinstance(value, str):
        s = value.strip()
        if not s:
            raise EncodeFrameError(f"枚举 {enum_name} 的值为空")
        try:
            if s.lower().startswith("0x"):
                return int(s, 16) & 0xFF
            if all(c.isdigit() or c in "+- " for c in s):
                return int(s, 0) & 0xFF
        except Exception:
            pass
        # 尝试字符串→枚举数字映射
        enums = cfg.get("enums", {}) or {}
        table = enums.get(enum_name, {}) or {}
        # 可能是 {"0":"OK"} 也可能是 {0:"OK"}；两种都反查
        for k, v in table.items():
            if isinstance(v, str) and v == s:
                if isinstance(k, int):
                    return k & 0xFF
                try:
                    return int(k, 0) & 0xFF
                except Exception:
                    continue
            if k == s:
                if isinstance(k, int):
                    return k & 0xFF
                try:
                    return int(k, 0) & 0xFF
                except Exception:
                    continue
        raise EncodeFrameError(f"枚举 {enum_name} 找不到值 {s!r}")
    raise EncodeFrameError(f"枚举 {enum_name} 不支持的类型 {type(value)!r}")


def _encode_attrid_int(attrid) -> int:
    """接受 0x10 / "0x10" / 16 三种形式。"""
    if isinstance(attrid, bool):
        return int(attrid) & 0xFF
    if isinstance(attrid, int):
        return attrid & 0xFF
    if isinstance(attrid, str):
        s = attrid.strip()
        if not s:
            raise EncodeFrameError("attrid 为空")
        try:
            if s.lower().startswith("0x"):
                return int(s, 16) & 0xFF
            return int(s, 0) & 0xFF
        except Exception as e:
            raise EncodeFrameError(f"attrid 编码失败：{attrid!r}，原因：{e}") from e
    raise EncodeFrameError(f"attrid 不支持类型 {type(attrid)!r}")


def _encode_scalar_value(cfg: dict, value, typeid: int | None, *, for_attr: bool):
    """把一个标量值编码成 bytes。
    - typeid 存在且为 attr：按 TYPEID_MAP 的 size/ctype/scale 反编码；
    - typeid 不存在：按 value 原始类型推断（int→1B/str(ascii)/bytes 直接用）
    """
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        s = value.strip()
        if for_attr and s.lower().startswith("0x") and len(s) % 2 == 0:
            try:
                return bytes.fromhex(s.replace(" ", "").replace("0x", ""))
            except Exception:
                pass
        return s.encode("utf-8")
    if isinstance(value, bool):
        return bytes([int(value)])

    # 数值：先处理 scale（编码侧 = 除以 scale）
    encode_value = value
    type_info = TYPEID_MAP.get(typeid) if (for_attr and typeid is not None) else None
    if type_info and isinstance(value, (int, float)):
        scale = type_info.get("scale")
        if scale:
            try:
                unscaled = value / float(scale)
                if abs(unscaled - round(unscaled)) < 1e-9:
                    encode_value = int(round(unscaled))
                else:
                    encode_value = unscaled
            except Exception:
                pass

    if isinstance(encode_value, int):
        if for_attr and typeid is not None:
            ti = TYPEID_MAP.get(typeid)
            if ti and ti.get("size"):
                signed = bool(ti.get("ctype", "").startswith("int"))
                byte_order = "little" if "_le" in ti.get("ctype", "") else "big"
                return int(encode_value).to_bytes(ti["size"], byte_order, signed=signed)
        nbytes = max(1, (encode_value.bit_length() + 7) // 8)
        if encode_value < 0:
            nbytes = max(1, ((encode_value + 1).bit_length() + 8) // 8)
        try:
            return int(encode_value).to_bytes(nbytes, "big", signed=(encode_value < 0))
        except Exception:
            nbytes += 1
            return int(encode_value).to_bytes(nbytes, "big", signed=True)
    if isinstance(encode_value, float):
        import struct as _struct
        return _struct.pack(">f", float(encode_value))
    raise EncodeFrameError(f"标量编码失败：不支持类型 {type(value)!r}（value={value!r}）")



def _encode_attr_list(cfg: dict, items: list) -> bytes:
    """items: [(attrid, value[, typeid]), ...]

    与 _parse_attr_list 完全对称的线格式：
        typeid(1B) + attrid(1B) + [len?] + value

    - 定长 typeid：不写 length 字段，value 长度由 TYPEID_MAP.size 决定
    - 变长 typeid (11/12/13/14/23/24)：写入 length 字段，宽度与解析端一致
      （默认 2 字节，可通过 attributes.__length_width__ 或 attr.meta.length_width 配置）
    """
    out = bytearray()
    if not isinstance(items, list):
        raise EncodeFrameError(f"attr_list 期望列表，实际 {type(items)!r}")

    DEFAULT_VARLEN_WIDTH = 2
    protocol_len_width = int((cfg.get("attributes") or {}).get("__length_width__", DEFAULT_VARLEN_WIDTH))
    if protocol_len_width not in (1, 2, 4):
        protocol_len_width = DEFAULT_VARLEN_WIDTH

    for it in items:
        if not isinstance(it, (list, tuple)):
            raise EncodeFrameError(f"attr_list 每项必须是 (attrid, value[, typeid])，实际 {it!r}")
        typeid: int | None = None
        if len(it) == 3:
            attrid, value, typeid = it
        elif len(it) == 2:
            attrid, value = it
        else:
            raise EncodeFrameError(f"attr_list 每项长度应为 2/3，实际 {len(it)}：{it!r}")

        typeid_i: int = 0 if typeid is None else _encode_attrid_int(typeid)
        type_info = TYPEID_MAP.get(typeid_i) if typeid_i else None
        attrid_i = _encode_attrid_int(attrid)
        attr_meta = _lookup_attr(cfg, attrid_i)

        val_raw = _encode_scalar_value(cfg, value, typeid_i, for_attr=True)

        # 顺序与解析端完全一致：typeid → attrid
        out.append(typeid_i & 0xFF)
        out.append(attrid_i & 0xFF)

        if typeid_i in VARLEN_TYPEIDS:
            # 变长：写入 length 字段（宽度与解析端对称）
            width_candidates = [
                attr_meta.get("length_width") if isinstance(attr_meta, dict) else None,
                protocol_len_width,
            ]
            width = DEFAULT_VARLEN_WIDTH
            for w in width_candidates:
                if isinstance(w, int) and w in (1, 2, 4):
                    width = w
                    break
            try:
                length_bytes = len(val_raw).to_bytes(width, "big", signed=False)
            except OverflowError as e:
                raise EncodeFrameError(
                    f"变长属性 value 长度 {len(val_raw)} 无法用 {width} 字节编码"
                ) from e
            out.extend(length_bytes)
            out.extend(val_raw)
        else:
            # 定长：不写 length，value 直接跟在后面
            expected_size = type_info.get("size") if type_info else None
            if expected_size is not None:
                if len(val_raw) > expected_size:
                    val_raw = val_raw[-expected_size:]  # 取低位
                elif len(val_raw) < expected_size:
                    val_raw = b"\x00" * (expected_size - len(val_raw)) + val_raw
            out.extend(val_raw)

    return bytes(out)


def _encode_attr_unit(cfg: dict, attrids) -> bytes:
    if isinstance(attrids, (list, tuple)):
        pass
    else:
        attrids = [attrids]
    out = bytearray()
    for a in attrids:
        out.append(_encode_attrid_int(a))
    return bytes(out)


def _encode_raw_bytes(raw, *, field_name: str = "raw") -> bytes:
    """把 HEX 字符串 / bytes / bytearray 统一转 bytes。"""
    if isinstance(raw, (bytes, bytearray)):
        return bytes(raw)
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return b""
        # 允许 "A5 A5 03 20..." / "A5A50320" / "0xA5A50320"
        s_clean = s.replace(" ", "").replace("\n", "").replace("\r", "").replace("\t", "")
        if s_clean.lower().startswith("0x"):
            s_clean = s_clean[2:]
        if len(s_clean) % 2 == 1:
            s_clean = "0" + s_clean
        try:
            return bytes.fromhex(s_clean)
        except Exception as e:
            raise EncodeFrameError(f"{field_name} HEX 解析失败：{raw!r}，原因：{e}") from e
    raise EncodeFrameError(f"{field_name} 需要 bytes / HEX 字符串，实际 {type(raw)!r}")


def _encode_cmd_data_by_format(cfg: dict, fmt: str, fields: dict) -> bytes:
    """按 JSON 里声明的 format 字符串编码 data 段字节。"""
    fmt = (fmt or "").lower()

    # --- 常见原子枚举 / 单值 ---
    if fmt in ("raw",):
        raw = fields.get("raw") if isinstance(fields, dict) else fields
        if raw is None:
            raw = fields.get("data", b"") if isinstance(fields, dict) else b""
        return _encode_raw_bytes(raw, field_name="data(raw)")

    if fmt in ("errcode", "module_status", "heartbeat_resp", "net_config_type",
               "mcu_status", "product_test_status", "product_test_cmd", "ota_sign_type"):
        # value 可以从 fields["value"] 取，也可以是 dict 直接 {"errcode":"OK"} 走字段
        if isinstance(fields, dict):
            candidates = [fields.get("value"), fields.get(fmt)]
            # 允许直接 {"errcode": "OK"} 这种
            val = next((c for c in candidates if c is not None), None)
            if val is None and len(fields) == 1:
                val = next(iter(fields.values()))
        else:
            val = fields
        if val is None:
            return b"\x00"
        return bytes([_enum_name_to_value(cfg, fmt, val)])

    if fmt in ("msg_id",):
        if isinstance(fields, dict):
            v = fields.get("msg_id") or fields.get("value") or 0
        else:
            v = fields
        return _encode_int(v, 1, "big", False)

    if fmt == "attr_unit":
        # fields 格式：{"attrids": [0x01, 0x02]} 或直接传 attrids 列表
        if isinstance(fields, dict):
            ids = fields.get("attrids", [])
        else:
            ids = fields
        return _encode_attr_unit(cfg, ids)

    if fmt in ("attr_list", "msg_id_then_attr", "msg_id_then_attr_unit",
               "errcode_then_attr", "event", "partition_pkg", "ota_crc",
               "ota_verify", "service_set", "get_time", "get_time_resp",
               "dev_info", "net_config", "msg_id_then_action",
               "errcode_then_partition", "ota_start", "product_test",
               "product_set"):
        # 这些复合格式：用户可以直接给 "raw" 字段当 data 段，由用户自己负责；
        # 当 fields 里没 raw 时，就按格式关键字做轻量编码（足够 GUI 表单使用）。
        if isinstance(fields, dict) and ("raw" in fields or "data" in fields):
            return _encode_raw_bytes(fields.get("raw") or fields.get("data", b""))

        # --- attr_list / msg_id_then_attr / errcode_then_attr / event：通用 attr ---
        if fmt in ("attr_list", "event"):
            if isinstance(fields, dict):
                items = fields.get("attrs") or fields.get("items") or []
            else:
                items = fields or []
            return _encode_attr_list(cfg, list(items) if items else [])

        if fmt == "msg_id":
            return b"\x00"

        if fmt == "msg_id_then_attr":
            if isinstance(fields, dict):
                mid = fields.get("msg_id") or 0
                items = fields.get("attrs") or fields.get("items") or []
            else:
                mid, items = 0, fields if isinstance(fields, (list, tuple)) else []
            buf = bytearray()
            buf += _encode_int(mid, 1, "big", False)
            buf += _encode_attr_list(cfg, list(items) if items else [])
            return bytes(buf)

        if fmt == "msg_id_then_attr_unit":
            if isinstance(fields, dict):
                mid = fields.get("msg_id") or 0
                ids = fields.get("attrids") or fields.get("attrs") or []
            else:
                mid, ids = 0, fields if isinstance(fields, (list, tuple)) else []
            buf = bytearray()
            buf += _encode_int(mid, 1, "big", False)
            buf += _encode_attr_unit(cfg, ids)
            return bytes(buf)

        if fmt == "errcode_then_attr":
            if isinstance(fields, dict):
                err = fields.get("errcode") or fields.get("value") or 0
                items = fields.get("attrs") or fields.get("items") or []
            else:
                err, items = 0, fields if isinstance(fields, (list, tuple)) else []
            buf = bytearray()
            buf += bytes([_enum_name_to_value(cfg, "errcode", err)])
            buf += _encode_attr_list(cfg, list(items) if items else [])
            return bytes(buf)

        if fmt == "msg_id_then_action":
            if isinstance(fields, dict):
                mid = fields.get("msg_id") or 0
                items = fields.get("actions") or fields.get("items") or fields.get("attrs") or []
            else:
                mid, items = 0, fields if isinstance(fields, (list, tuple)) else []
            buf = bytearray()
            buf += _encode_int(mid, 1, "big", False)
            buf += _encode_attr_list(cfg, list(items) if items else [])
            return bytes(buf)

        if fmt in ("partition_pkg", "errcode_then_partition"):
            # partition(uint16_be) + pkg(uint8) + fw_data(bytes) / 或前面加 errcode(uint8)
            if isinstance(fields, dict):
                err = fields.get("errcode")
                part = fields.get("partition") or 0
                pkg = fields.get("package") or fields.get("pkg") or 0
                data = _encode_raw_bytes(fields.get("data") or fields.get("fw_data") or b"")
            else:
                err, part, pkg, data = None, 0, 0, b""
            buf = bytearray()
            if fmt == "errcode_then_partition":
                buf += bytes([_enum_name_to_value(cfg, "errcode", (err if err is not None else 0))])
            buf += _encode_int(part, 2, "big", False)
            buf += _encode_int(pkg, 1, "big", False)
            buf += data
            return bytes(buf)

        if fmt in ("ota_crc",):
            if isinstance(fields, dict):
                part = fields.get("partition") or 0
                crc = fields.get("crc32") or fields.get("crc") or 0
            else:
                part, crc = 0, 0
            buf = bytearray()
            buf += _encode_int(part, 2, "big", False)
            buf += _encode_int(crc, 4, "big", False)
            return bytes(buf)

        if fmt in ("ota_verify",):
            if isinstance(fields, dict):
                st = fields.get("ota_sign_type") or fields.get("sign_type") or 1
                raw = _encode_raw_bytes(fields.get("sign_value") or fields.get("value") or fields.get("data") or b"")
            else:
                st, raw = 1, _encode_raw_bytes(fields or b"")
            buf = bytearray()
            buf += bytes([_enum_name_to_value(cfg, "ota_sign_type", st)])
            buf += raw
            return bytes(buf)

        if fmt in ("ota_start",):
            if isinstance(fields, dict):
                raw = _encode_raw_bytes(fields.get("raw") or fields.get("data") or fields.get("meta") or b"")
            else:
                raw = _encode_raw_bytes(fields or b"")
            # 简单：按 ota_start 格式就原样 raw（产品协议可自行 override）
            return raw

        if fmt in ("service_set",):
            if isinstance(fields, dict):
                raw = _encode_raw_bytes(fields.get("raw") or fields.get("data") or b"")
            else:
                raw = _encode_raw_bytes(fields or b"")
            return raw

        if fmt in ("net_config",):
            if isinstance(fields, dict):
                t = fields.get("net_config_type") or fields.get("value") or 1
                raw = _encode_raw_bytes(fields.get("raw") or fields.get("data") or fields.get("extra") or b"")
            else:
                t, raw = 1, _encode_raw_bytes(fields or b"")
            buf = bytearray()
            buf += bytes([_enum_name_to_value(cfg, "net_config_type", t)])
            buf += raw
            return bytes(buf)

        if fmt in ("get_time",):
            if isinstance(fields, dict):
                tz = fields.get("timezone") or fields.get("tz") or 0
            else:
                tz = fields or 0
            return _encode_int(tz, 1, "big", True)

        if fmt in ("get_time_resp",):
            if isinstance(fields, dict):
                err = fields.get("errcode") or 0
                tz = fields.get("timezone") or fields.get("tz") or 0
                year = fields.get("year") or 0
                month = fields.get("month") or 0
                day = fields.get("day") or 0
                weekday = fields.get("weekday") or 0
                hour = fields.get("hour") or 0
                minute = fields.get("minute") or 0
                second = fields.get("second") or 0
            else:
                err, tz, year, month, day, weekday, hour, minute, second = 0, 0, 0, 0, 0, 0, 0, 0, 0
            buf = bytearray()
            buf += bytes([_enum_name_to_value(cfg, "errcode", err)])
            buf += _encode_int(tz, 1, "big", True)
            for v in (year, month, day, weekday, hour, minute, second):
                buf += _encode_int(v, 1, "big", False)
            return bytes(buf)

        if fmt in ("dev_info", "product_test", "product_set"):
            if isinstance(fields, dict):
                raw = _encode_raw_bytes(fields.get("raw") or fields.get("data") or b"")
            else:
                raw = _encode_raw_bytes(fields or b"")
            return raw

        # 兜底：按 raw 解析空数据（至少能发一个空命令）
        return b""

    # 未知 format：如果用户给了 raw 就用 raw，否则空 data 段
    if isinstance(fields, dict):
        if "raw" in fields or "data" in fields:
            return _encode_raw_bytes(fields.get("raw") or fields.get("data", b""))
    return b""


def encode_frame(
    cmd_code,
    cfg: dict,
    *,
    direction: str = "request",
    fields: dict | list | None = None,
    data: bytes | str | None = None,
) -> bytes:
    """根据 V3.0 帧结构把命令 + 字段字典组包成一条完整帧 bytes。

    参数：
      cmd_code   : 0x20 / "0x20" / 32
      cfg        : 协议配置（内置 V3 或 merge 之后的产品协议，需要包含 frame）
      direction  : "request" 或 "response"（决定用 command 里的 request/response format）
      fields     : 数据段字段字典，具体键由 format 决定；也可以直接传 attrs 列表
      data       : 优先级最高；如果给了 bytes/HEX 字符串，直接当 data 段（跳过 format 编码）

    示例：
        bytes = encode_frame(0x20, cfg, direction="request", fields={"value": 1})
                  → heartbeat req，module_status=1

        bytes = encode_frame("0x01", cfg, fields={"msg_id": 7, "attrs": [(0x12, True, 0x01)]})
                  → msg_id_then_attr：msg_id=7，attrid=0x12 type=bool value=True
    """
    if not isinstance(cfg, dict):
        raise ProtocolConfigError("encode_frame 需要 dict 类型的协议 cfg")
    frame_cfg = cfg.get("frame", {}) or {}
    header_size = int(frame_cfg.get("header_size", 2))
    header_raw = frame_cfg.get("header", "0xA5A5")
    ver_offset = int(frame_cfg.get("ver_offset", 2))
    ver_size = int(frame_cfg.get("ver_size", 1))
    cmd_offset = int(frame_cfg.get("cmd_offset", 3))
    length_offset = int(frame_cfg.get("length_offset", 4))
    length_size = int(frame_cfg.get("length_size", 2))
    length_byte_order = frame_cfg.get("length_byte_order", "big")
    ver_raw = frame_cfg.get("ver", "0x03")
    cs_cfg = frame_cfg.get("checksum", {}) or {}
    cs_algo = cs_cfg.get("algorithm", "sum").lower()
    cs_len = int(cs_cfg.get("length", 1))

    # 解析 cmd_code
    cmd_int = 0
    try:
        if isinstance(cmd_code, bool):
            cmd_int = int(cmd_code)
        elif isinstance(cmd_code, int):
            cmd_int = cmd_code
        elif isinstance(cmd_code, str):
            s = cmd_code.strip()
            cmd_int = _parse_int(s if s else "0")
        else:
            cmd_int = int(cmd_code)
    except Exception as e:
        raise EncodeFrameError(f"cmd_code 解析失败：{cmd_code!r}，原因：{e}") from e
    cmd_int &= 0xFF

    # 方向
    if direction is None:
        direction = "request"
    direction = str(direction).strip().lower() or "request"
    if direction not in ("request", "response"):
        raise EncodeFrameError(f"direction 只能是 'request' 或 'response'，实际 {direction!r}")

    # 按命令找到 format（找不到就默认 raw）
    commands = cfg.get("commands", []) or []
    chosen_def: dict = {"format": "raw", "name": ""}
    for c in commands:
        this_code_s = c.get("cmd_code", "")
        this_code_int: int | None = None
        try:
            this_code_int = _parse_int(this_code_s) if this_code_s else None
        except Exception:
            this_code_int = None
        matched = (this_code_int is not None and (this_code_int & 0xFF) == cmd_int)
        if matched:
            dir_block = c.get(direction, {}) or {}
            if isinstance(dir_block, dict) and "format" in dir_block:
                chosen_def = dir_block
            elif isinstance(c.get("format"), str):
                # 老格式：{format, direction} 平层 command（兼容 joymay 等产品协议）
                chosen_def = {"format": c.get("format", "raw"), "name": c.get("name", "")}
            break

    fmt = chosen_def.get("format") or "raw"

    # 1) 如果 data 显式给了，直接用 data
    data_bytes = b""
    if data is not None:
        data_bytes = _encode_raw_bytes(data, field_name="data")
    else:
        # 2) 否则按 format 从 fields 编码
        if fields is None:
            fields_payload: dict | list = {} if fmt not in ("attr_list", "event", "msg_id_then_attr",
                                                             "msg_id_then_attr_unit",
                                                             "errcode_then_attr",
                                                             "msg_id_then_action") else []
        else:
            fields_payload = fields
        try:
            data_bytes = _encode_cmd_data_by_format(cfg, fmt, fields_payload)
        except EncodeFrameError:
            raise
        except ProtocolError:
            raise
        except Exception as e:
            raise EncodeFrameError(f"按 format={fmt} 编码 data 段失败：{e}") from e

    # data_len
    data_len = len(data_bytes)
    try:
        length_bytes = data_len.to_bytes(length_size, length_byte_order, signed=False)
    except Exception as e:
        raise EncodeFrameError(f"数据长度 {data_len} 无法用 {length_size} 字节编码（order={length_byte_order}）：{e}") from e

    # header_bytes / ver_bytes
    header_bytes = _encode_raw_bytes(str(header_raw), field_name="header")
    if len(header_bytes) < header_size:
        header_bytes = b"\x00" * (header_size - len(header_bytes)) + header_bytes
    elif len(header_bytes) > header_size:
        header_bytes = header_bytes[-header_size:]
    ver_bytes_raw = _encode_raw_bytes(str(ver_raw), field_name="ver")
    if len(ver_bytes_raw) < ver_size:
        ver_bytes_raw = b"\x00" * (ver_size - len(ver_bytes_raw)) + ver_bytes_raw
    elif len(ver_bytes_raw) > ver_size:
        ver_bytes_raw = ver_bytes_raw[-ver_size:]

    cmd_byte = bytes([cmd_int])

    # 拼出"帧头 + ver + cmd + length + data"主体（不含 checksum）
    pre_body = bytearray()
    # 前 header_size 字节 → header
    pre_body.extend(header_bytes)
    # 接下来从 ver_offset 到 ver_offset+ver_size：先 pad 零再塞 ver_bytes_raw
    # 简化：按 V3 声明的顺序 —— header(2) + ver(1) + cmd(1) + length(2) + data(n) + cs(1)
    # 对于自定义 offset 的协议，用 bytearray 先拉长再写各段（允许段之间有 pad）
    total_no_cs = length_offset + length_size + data_len
    body = bytearray(total_no_cs)
    # header
    body[0:header_size] = header_bytes
    # ver
    body[ver_offset:ver_offset + ver_size] = ver_bytes_raw
    # cmd
    if cmd_offset + 1 > total_no_cs:
        raise EncodeFrameError(f"cmd_offset={cmd_offset} 超出帧主体长度 {total_no_cs}")
    body[cmd_offset:cmd_offset + 1] = cmd_byte
    # length
    if length_offset + length_size > total_no_cs:
        raise EncodeFrameError(f"length_offset={length_offset} 超出帧主体长度 {total_no_cs}")
    body[length_offset:length_offset + length_size] = length_bytes
    # data
    data_start = length_offset + length_size
    if data_start + data_len > total_no_cs:
        raise EncodeFrameError(
            f"data 段位置溢出：data_start={data_start} data_len={data_len} total_no_cs={total_no_cs}"
        )
    if data_len > 0:
        body[data_start:data_start + data_len] = data_bytes

    # 校验和：covers 目前只实现 from_start_to_checksum_exclusive（对 body 全体求校验）
    if cs_len <= 0 or cs_algo == "none":
        return bytes(body)
    try:
        cs_bytes = calc_checksum(bytes(body), cs_algo)
    except ChecksumAlgoError:
        # 未知算法兜底：不发校验
        return bytes(body)
    if len(cs_bytes) < cs_len:
        cs_bytes = b"\x00" * (cs_len - len(cs_bytes)) + cs_bytes
    elif len(cs_bytes) > cs_len:
        cs_bytes = cs_bytes[-cs_len:]
    return bytes(body) + cs_bytes
