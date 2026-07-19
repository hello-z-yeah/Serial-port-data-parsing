"""协议监控工具：串口实时采集 + 粘贴交互解析。

提供两种模式：
- serial:  连接串口，实时接收并解析 V3.0 协议帧
- paste:   交互式粘贴 hex 数据，立即解析（支持多条）
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import TextIO

from .parser import ParseResult, ProtocolError, load_protocol, parse_frame, parse_hex_input, to_hex
from .serial_collector import FrameSynchronizer, SerialCollector


# ---------- 渲染 ----------

def _format_timestamp(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%H:%M:%S.%f")[:-3]


def render_result_compact(result: ParseResult, ts: float | None = None) -> str:
    """紧凑单行输出（用于实时监控）。"""
    ts_str = f"[{_format_timestamp(ts)}] " if ts else ""
    cs = "✓" if result.checksum_ok else "✗" if result.checksum_ok is False else " "
    status = "OK" if not result.error else "ERR"
    dir_label = f" [{result.direction}]" if result.direction else ""
    return f"{ts_str}{status} {cs} {result.cmd_code:<6} {result.cmd_name}{dir_label}  | {result.raw_hex}"


def render_result_detail(result: ParseResult, ts: float | None = None) -> str:
    """详细多行输出（用于粘贴模式）。"""
    lines: list[str] = []
    if ts:
        lines.append(f"时间: {_format_timestamp(ts)}")
    lines.append(f"原始: {result.raw_hex}")
    lines.append(f"命令: {result.cmd_code}  {result.cmd_name}")
    if result.direction:
        lines.append(f"方向: {result.direction}")
    if result.description:
        lines.append(f"说明: {result.description}")
    if result.checksum_ok is not None:
        lines.append(f"校验: {'通过' if result.checksum_ok else '失败'}")
    if result.length_match is False:
        lines.append(f"长度: 不匹配（length字段与实际不一致）")
    if result.error:
        lines.append(f"错误: {result.error}")
    if result.fields:
        lines.append("字段:")
        for f in result.fields:
            name = f.get("name", "")
            text = f.get("text", "")
            lines.append(f"  · {name:<24} {text}")
    return "\n".join(lines)


# ---------- 日志 ----------

class ResultLogger:
    """把解析结果保存到日志文件。"""

    def __init__(self, path: str | Path, mode: str = "compact"):
        self.path = Path(path)
        self.mode = mode  # compact / detail
        self._f: TextIO | None = None
        self._count = 0

    def __enter__(self) -> "ResultLogger":
        self._f = self.path.open("a", encoding="utf-8")
        self._f.write(f"\n===== 开始记录 {datetime.now().isoformat(timespec='seconds')} =====\n")
        self._f.flush()
        return self

    def __exit__(self, *args) -> None:
        if self._f:
            self._f.write(f"===== 结束记录（共 {self._count} 条） =====\n")
            self._f.close()

    def log(self, result: ParseResult, ts: float | None = None) -> None:
        if not self._f:
            return
        ts = ts or time.time()
        if self.mode == "detail":
            self._f.write(f"--- {_format_timestamp(ts)} ---\n")
            self._f.write(render_result_detail(result) + "\n\n")
        else:
            self._f.write(render_result_compact(result, ts) + "\n")
        self._f.flush()
        self._count += 1


# ---------- 粘贴交互模式 ----------

def run_paste_mode(cfg: dict, logger: ResultLogger | None = None) -> int:
    """交互式粘贴解析。

    用户粘贴 hex 数据后按回车，立即解析。
    输入空行退出。
    支持一次粘贴多条（换行分隔）。
    """
    product = cfg.get("product", "unknown")
    print(f"=== 协议解析工具 - 粘贴模式 (产品: {product}) ===")
    print("粘贴 hex 数据（支持空格/逗号分隔，一行一条），按回车解析。")
    print("输入空行退出。\n")

    sync = FrameSynchronizer(cfg)
    line_count = 0

    while True:
        try:
            line = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            break

        if not line:
            if line_count == 0:
                print("已退出。")
                break
            continue

        line_count += 1

        # 尝试整行解析为一条完整指令
        try:
            data = parse_hex_input(line)
            result = parse_frame(data, cfg)
            print()
            print(render_result_detail(result))
            print()
            if logger:
                logger.log(result)
        except ProtocolError as e:
            # 整行解析失败，可能是帧流数据 — 用同步器试试
            print(f"\n[!] 整行解析失败: {e}")
            print("    尝试作为字节流进行帧同步...")
            try:
                data = parse_hex_input(line)
                frames = sync.feed(data)
                if frames:
                    for frame in frames:
                        result = parse_frame(frame.raw, cfg)
                        print()
                        print(render_result_detail(result))
                        print()
                        if logger:
                            logger.log(result)
                    print(f"    共提取 {len(frames)} 帧。")
                else:
                    print("    未提取到完整帧（可能数据不足）。")
                    print(f"    缓冲区剩余 {sync.partial_bytes} 字节。")
            except ProtocolError as e2:
                print(f"    也失败: {e2}\n")

    return 0


# ---------- 串口实时模式 ----------

def run_serial_mode(
    cfg: dict,
    port: str,
    baudrate: int = 115200,
    detail: bool = False,
    logger: ResultLogger | None = None,
) -> int:
    """串口实时采集解析。"""
    product = cfg.get("product", "unknown")
    print(f"=== 协议解析工具 - 串口实时模式 ===")
    print(f"产品: {product}")
    print(f"串口: {port} @ {baudrate} bps")
    print(f"模式: {'详细' if detail else '紧凑'}")
    print("按 Ctrl+C 停止\n")

    def on_frame(result: ParseResult, frame, ts: float) -> None:
        if detail:
            print(render_result_detail(result, ts))
            print("-" * 60)
        else:
            print(render_result_compact(result, ts))
        if logger:
            logger.log(result, ts)

    def on_error(msg: str) -> None:
        print(f"[错误] {msg}")

    collector = SerialCollector(
        cfg=cfg,
        port=port,
        baudrate=baudrate,
        on_frame=on_frame,
        on_error=on_error,
    )

    try:
        collector.start()
    except Exception as e:
        print(f"打开串口失败: {e}")
        return 2

    print(f"[已连接] 等待数据...\n")

    try:
        while collector.running:
            time.sleep(0.1)
    except KeyboardInterrupt:
        print("\n\n正在停止...")
    finally:
        collector.stop()

    if collector.sync:
        print(f"共接收 {collector.sync.frame_count} 帧，错误 {collector.sync.error_count} 次。")
    return 0


def list_serial_ports() -> list[dict]:
    """列出所有可用串口。"""
    return SerialCollector.list_ports()
