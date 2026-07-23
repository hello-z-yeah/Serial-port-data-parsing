"""在线更新会话快照（Save / Load / Clear）。

目标：
- 用户点击"下载并更新"时，如果串口正在接收，先安全停止、刷写缓冲区未写入磁盘的原始数据/日志，
  再把串口号、波特率、协议文件路径、是否继续接收、发送方、数据格式（HEX/ASCII）、
  日志路径、原始数据保存路径等持久化到临时配置 JSON 文件；
- 新程序拉起后自动读取这个快照 → 恢复协议 + 串口 + 波特率 + 是否开始接收；
  成功后清理快照，失败（例如串口被拔掉）弹友好提示，绝不崩。

快照文件位置：
- EXE 模式：旧 EXE 同目录下 `_update_session.json`（和 bat/vbs 一起，bat 不会删它）
- 开发模式：项目根目录 `_update_session.json`
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


SESSION_FILENAME = "_update_session.json"


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class SessionSnapshot:
    """更新重启时需要恢复的"会话快照"。

    所有字段都有默认值，保证未来拓展字段时，读旧快照也不崩。
    """

    # 是否在更新前正在接收（如果 True，重启后自动 start_serial）
    was_collecting: bool = False

    # 串口配置
    port: str = ""
    baudrate: int = 115200
    bytesize: int = 8
    stopbits: float = 1.0

    # 协议
    product_name: str = ""                 # 产品名（下拉显示的中文/用户自定义名）
    product_source: str = ""               # product_sources.get(name)，__builtin_v3__ 或路径

    # 数据格式
    is_hex_format: bool = True             # True=HEX, False=ASCII
    direction: str = ""                    # request/response/""（发送方）
    detail_mode: bool = False              # 详细模式

    # 持久化：日志/原始数据路径
    log_path: str = ""                     # 如果正在写日志，路径留在这里（重启后若文件存在则继续记录）
    save_raw_enabled: bool = False
    save_raw_path: str = ""
    save_raw_filename: str = ""

    # 发送面板 + 周期发送：冷更新时无感恢复
    tx_send_mode: str = ""                 # protocol / raw_hex / raw_ascii
    tx_cmd_code: str = ""                  # 例如 0x20
    tx_direction: str = ""                 # 模组发送 / MCU发送
    tx_fields_json: str = ""               # 协议模式 fields JSON 文本
    tx_raw: str = ""                       # Raw HEX/ASCII 文本
    tx_cycle_enabled: bool = False         # 是否正在循环
    tx_interval_ms: int = 1000             # 循环间隔 ms

    # 版本标签（方便未来迁移）
    version: int = 1

    # 额外扩展（字典，不用改结构就能加字段）
    extras: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

def default_session_path() -> Path:
    """默认快照路径：

    - PyInstaller EXE：sys.executable 同目录（和 _updater.bat / vbs 一起，保证 bat 替换过程中它不被删）
    - 开发模式：项目根目录
    """
    try:
        if getattr(sys, "frozen", False):
            d = Path(sys.executable).resolve().parent
        else:
            d = Path(__file__).resolve().parent.parent
    except Exception:
        d = Path(tempfile.gettempdir())
    try:
        d.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return d / SESSION_FILENAME


# ---------------------------------------------------------------------------
# 持久化 API
# ---------------------------------------------------------------------------

def save_snapshot(snap: SessionSnapshot, path: str | Path | None = None) -> Path:
    """把快照写盘。写入失败时抛 UpdaterError（上层 classify_protocol_error 会友好提示）。

    策略：先写临时文件再 os.replace，保证 crash 时旧快照不被半写损坏。
    """
    target = Path(path) if path else default_session_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".tmp")
        payload = asdict(snap)
        with open(tmp, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    except Exception as e:
        from protocol_parser.parser import _UpdaterError_proxy
        # 这里用函数创建避免强 import 循环
        raise _mk_snapshot_error(
            message=f"save session snapshot failed: {e}",
            friendly_msg="保存更新会话快照失败，请检查磁盘或文件权限后重试。",
        ) from e
    return target


def load_snapshot(path: str | Path | None = None) -> SessionSnapshot | None:
    """读取快照。文件不存在/损坏/缺字段都返回 None（不崩主流程）。"""
    target = Path(path) if path else default_session_path()
    if not target.exists():
        return None
    try:
        with open(target, "r", encoding="utf-8") as fp:
            payload = json.load(fp)
    except Exception:
        # 坏文件直接清理掉，避免下次再读
        try:
            target.unlink(missing_ok=True)
        except Exception:
            pass
        return None
    if not isinstance(payload, dict):
        return None
    try:
        snap = SessionSnapshot()
        for k, v in payload.items():
            if hasattr(snap, k):
                setattr(snap, k, v)
        extras = payload.get("extras")
        if isinstance(extras, dict):
            snap.extras = dict(extras)
        return snap
    except Exception:
        return None


def clear_snapshot(path: str | Path | None = None) -> None:
    """清理快照。失败静默，不影响主流程。"""
    target = Path(path) if path else default_session_path()
    try:
        if target.exists():
            target.unlink()
    except Exception:
        pass


def snapshot_exists(path: str | Path | None = None) -> bool:
    target = Path(path) if path else default_session_path()
    return target.exists()


# ---------------------------------------------------------------------------
# 内部：创建错误（运行时从 parser 拿 UpdaterError 类，避免循环 import）
# ---------------------------------------------------------------------------

def _mk_snapshot_error(message: str, friendly_msg: str):
    from protocol_parser.parser import UpdaterError
    return UpdaterError(message=message, friendly_msg=friendly_msg)
