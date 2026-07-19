"""串口帧同步与数据采集模块。

从连续的字节流中识别并切出完整的 V3.0 协议帧，
供解析器使用。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from .parser import (
    Frame,
    ParseResult,
    ProtocolError,
    load_protocol,
    parse_frame,
    split_frame,
    to_hex,
)

try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False


@dataclass
class FrameSynchronizer:
    """字节流帧同步器：从任意位置输入字节，输出完整帧。

    工作原理：
    1. 累积字节到缓冲区
    2. 找到帧头（header）
    3. 读取长度字段
    4. 等收齐完整帧（header + ver + cmd + length + data + chk）
    5. 切出一帧并返回
    6. 循环直到缓冲区不足一帧
    """

    cfg: dict
    buffer: bytearray = field(default_factory=bytearray)
    frame_count: int = 0
    error_count: int = 0
    partial_bytes: int = 0

    def feed(self, data: bytes) -> list[Frame]:
        """输入一段字节，返回解析出的所有完整帧。"""
        self.buffer.extend(data)
        self.partial_bytes = len(self.buffer)
        frames: list[Frame] = []

        while True:
            frame = self._try_extract_one()
            if frame is None:
                break
            frames.append(frame)
            self.frame_count += 1

        self.partial_bytes = len(self.buffer)
        return frames

    def _try_extract_one(self) -> Frame | None:
        """尝试从缓冲区头部提取一帧。"""
        buf = self.buffer
        frame_cfg = self.cfg.get("frame", {})
        header_size = frame_cfg.get("header_size", 2)
        expected_header = _parse_int(frame_cfg.get("header", "0xA5A5"))
        ver_offset = frame_cfg.get("ver_offset", 2)
        ver_size = frame_cfg.get("ver_size", 1)
        cmd_offset = frame_cfg.get("cmd_offset", 3)
        length_offset = frame_cfg.get("length_offset", 4)
        length_size = frame_cfg.get("length_size", 2)
        length_byte_order = frame_cfg.get("length_byte_order", "big")
        checksum_size = frame_cfg.get("checksum", {}).get("length", 1)

        min_header = length_offset + length_size  # 至少要读到长度字段

        while len(buf) >= header_size:
            # 查找帧头
            header_val = int.from_bytes(buf[:header_size], "big")
            if header_val == expected_header:
                break
            # 帧头不匹配，移进一个字节再试
            self.error_count += 1
            buf.pop(0)

        if len(buf) < min_header:
            return None  # 数据不足，等更多

        # 读取 length 字段
        data_len = int.from_bytes(
            bytes(buf[length_offset:length_offset + length_size]),
            byteorder=length_byte_order,
        )

        # 计算总帧长
        total_len = length_offset + length_size + data_len + checksum_size

        # 防止异常长度（比如帧头误识别导致 length 很大）
        max_frame = frame_cfg.get("max_frame_size", 4096)
        if data_len > max_frame:
            # 异常长度，可能帧头识别错了，移进一个字节继续找
            self.error_count += 1
            buf.pop(0)
            return None  # 让外层循环继续

        if len(buf) < total_len:
            return None  # 数据还没收齐

        # 提取完整帧
        raw = bytes(buf[:total_len])
        del buf[:total_len]

        # 用 split_frame 正式拆分（同时做校验等）
        try:
            return split_frame(raw, self.cfg)
        except ProtocolError:
            # 拆分失败（比如版本不对），这帧可能是垃圾，丢掉
            self.error_count += 1
            return None

    def reset(self) -> None:
        """清空缓冲区。"""
        self.buffer.clear()
        self.partial_bytes = 0


@dataclass
class SerialCollector:
    """串口数据采集器：连接串口，实时解析帧，回调输出。"""

    cfg: dict
    port: str
    baudrate: int = 115200
    on_frame: Callable[[ParseResult, Frame, float], None] | None = None
    on_error: Callable[[str], None] | None = None
    running: bool = False
    _thread: threading.Thread | None = None
    _serial: "serial.Serial | None" = None
    sync: FrameSynchronizer | None = None

    def start(self) -> None:
        if not HAS_SERIAL:
            raise RuntimeError("pyserial 未安装，请执行: pip install pyserial")
        if self.running:
            return
        self.sync = FrameSynchronizer(self.cfg)
        self._serial = serial.Serial(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=0.1,
        )
        self.running = True
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        if self._thread:
            self._thread.join(timeout=1.0)
        if self._serial and self._serial.is_open:
            self._serial.close()

    def _read_loop(self) -> None:
        assert self._serial is not None and self.sync is not None
        try:
            while self.running:
                try:
                    raw = self._serial.read(4096)
                except serial.SerialException as e:
                    if self.on_error:
                        self.on_error(f"串口读取错误: {e}")
                    break

                if not raw:
                    continue

                frames = self.sync.feed(raw)
                now = time.time()
                for frame in frames:
                    result = parse_frame(frame.raw, self.cfg)
                    if self.on_frame:
                        self.on_frame(result, frame, now)
        except Exception as e:
            if self.on_error:
                self.on_error(f"采集异常: {e}")

    @staticmethod
    def list_ports() -> list[dict]:
        """列出当前所有可用串口。"""
        if not HAS_SERIAL:
            return []
        ports = []
        for p in serial.tools.list_ports.comports():
            ports.append({
                "device": p.device,
                "description": p.description,
                "hwid": p.hwid,
            })
        return ports


def _parse_int(v) -> int:
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s.startswith("0x"):
            return int(s, 16)
        return int(s, 0)
    raise ProtocolError(f"无法解析为整数: {v}")
